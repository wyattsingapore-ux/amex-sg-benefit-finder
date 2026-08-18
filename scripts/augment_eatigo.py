#!/usr/bin/env python3
"""Add the full Eatigo Singapore restaurant list to merchants.json.

Scope is intentionally simple:
- discover Eatigo restaurant/branch membership across all result pages;
- keep the Eatigo branch link;
- enrich with public street addresses when available for mapping and LC matching;
- do NOT collect time slots or discount percentages.

The restaurant list is the primary dataset. Address lookup is best-effort and
must never make a complete Eatigo list fail to deploy. Only verified addresses
are eligible for map pins and Eatigo+LC matching.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from scripts import refresh_data as base

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEARCH_URL = "https://eatigo.com/en/regions/27/search"
BASE_URL = "https://eatigo.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"

BRANCH_RE = re.compile(r"/branches/(\d+)")
RESULT_COUNT_RE = re.compile(r"1\s*-\s*\d+\s+of\s+([\d,]+)\s+results", re.I)
RATING_TAIL_RE = re.compile(
    r"\s+(?:NEW\s+)?\d(?:\.\d)?(?:\s+[\d.]+[kKmM]?\s+reservations)?(?:\s+(?:Hot|New))?\s*$",
    re.I,
)
TIME_TEXT_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s*-\s*\d{1,3}\s*%\s*$", re.I)
GENERIC_NAME_WORDS = {
    "the", "and", "restaurant", "restaurants", "cafe", "café", "bar", "grill",
    "buffet", "kitchen", "dining",
}
NON_SG_HINTS = (
    "johor", "tebrau", "southkey", "iskandar puteri", "mount austin", "komtar jbcc",
    "paradigm mall jb", "aeon mall", "sutera mall", "city square jb",
)
BRANCH_CACHE = DATA / "eatigo_branch_cache.json"
SNAPSHOT = DATA / "eatigo_snapshot.json"
BRANCH_CACHE_MAX_AGE = timedelta(days=60)
SNAPSHOT_MAX_AGE = timedelta(days=7)


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip(" \t\r\n|,-")


def key_text(value: str | None) -> str:
    s = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_key(value: str | None) -> str:
    toks = [t for t in key_text(value).split() if t not in GENERIC_NAME_WORDS]
    return " ".join(toks)


def display_name_from_anchor(text: str) -> str:
    s = clean(text)
    s = RATING_TAIL_RE.sub("", s).strip()
    return re.sub(r"\s+NEW$", "", s, flags=re.I).strip()


def split_listing_name(value: str) -> tuple[str, str]:
    parts = re.split(r"\s+@\s+", clean(value), maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


def looks_non_singapore(name: str) -> bool:
    k = key_text(name)
    return any(hint in k for hint in NON_SG_HINTS)


def text_score(a: str, b: str) -> float:
    a, b = name_key(a), name_key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.94
    return SequenceMatcher(None, a, b).ratio()


def head_score(eatigo: dict, merchant: dict) -> float:
    head, _ = split_listing_name(eatigo.get("name") or "")
    return max(text_score(head, merchant.get("name") or ""), text_score(head, merchant.get("brand") or ""))


def same_location(eatigo: dict, merchant: dict) -> bool:
    if eatigo.get("address_verified") is False:
        return False
    ep, mp = eatigo.get("postal_code"), merchant.get("postal_code")
    if ep and mp:
        return str(ep) == str(mp)
    ea = base.norm_address(eatigo.get("address"))
    ma = base.norm_address(merchant.get("address"))
    if ea and ma and (ea in ma or ma in ea):
        return True
    return SequenceMatcher(None, ea, ma).ratio() >= 0.86 if ea and ma else False


def choose_lc_match(eatigo: dict, merchants: list[dict], used: set[int]) -> tuple[int | None, str | None]:
    if eatigo.get("address_verified") is False:
        return None, None
    candidates = []
    for i, m in enumerate(merchants):
        if i in used or not m.get("lc") or m.get("category") != "dining":
            continue
        if not same_location(eatigo, m):
            continue
        hs = head_score(eatigo, m)
        if hs >= 0.82:
            candidates.append((i, hs))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[1], reverse=True)
    best = candidates[0]
    if len(candidates) == 1 or best[1] - candidates[1][1] >= 0.08:
        return best[0], f"Eatigo+LC outlet+location match ({best[1]:.2f})"
    return None, None


def branch_url(href: str) -> str:
    u = urlparse(urljoin(BASE_URL, href))
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", "", ""))


async def anchors_for_page(page) -> list[dict]:
    return await page.locator('a[href*="/branches/"]').evaluate_all(
        """els => els.map(a => ({
            href: a.href || a.getAttribute('href') || '',
            text: (a.innerText || a.textContent || '').trim()
        }))"""
    )


def aggregate_anchors(rows: list[dict], found: dict[str, dict]) -> set[str]:
    page_ids: set[str] = set()
    for row in rows:
        href = clean(row.get("href"))
        text = clean(row.get("text"))
        m = BRANCH_RE.search(href)
        if not m:
            continue
        bid = m.group(1)
        page_ids.add(bid)
        if not text or text.lower() in {"more", "view more", "today", "tomorrow"} or TIME_TEXT_RE.match(text):
            continue
        name = display_name_from_anchor(text)
        if not name:
            continue
        current = found.get(bid)
        if current is None or len(name) > len(current["name"]):
            found[bid] = {"branch_id": bid, "name": name, "url": branch_url(href)}
    return page_ids


async def click_page_number(page, page_num: int, previous_ids: set[str]) -> None:
    clicked = await page.evaluate(
        """n => {
          const els = [...document.querySelectorAll('button,a')]
            .filter(e => (e.textContent || '').trim() === String(n) && e.offsetParent !== null);
          if (!els.length) return false;
          els.sort((a,b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
          els[0].click();
          return true;
        }""",
        page_num,
    )
    if not clicked:
        # Fallback for compact pagination windows: use the visible next control.
        clicked = await page.evaluate(
            """() => {
              const els = [...document.querySelectorAll('button,a')].filter(e => e.offsetParent !== null);
              const next = els.find(e => {
                const t = (e.textContent || '').trim().toLowerCase();
                const a = (e.getAttribute('aria-label') || '').toLowerCase();
                return t === 'next' || t === '›' || t === '>' || a.includes('next');
              });
              if (!next) return false;
              next.click();
              return true;
            }"""
        )
    if not clicked:
        raise RuntimeError(f"Could not find Eatigo pagination control for page {page_num}")

    for _ in range(40):
        await page.wait_for_timeout(300)
        rows = await anchors_for_page(page)
        ids = {BRANCH_RE.search(x.get("href", "")).group(1) for x in rows if BRANCH_RE.search(x.get("href", ""))}
        if ids and ids != previous_ids:
            return
    raise RuntimeError(f"Eatigo page {page_num} did not load a new restaurant set")


async def discover_eatigo_browser() -> tuple[list[dict], int]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        context = await browser.new_context(user_agent=UA, locale="en-SG")
        page = await context.new_page()
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector('a[href*="/branches/"]', timeout=30000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Eatigo search page did not expose restaurant links") from exc
        await page.wait_for_timeout(1200)

        body = await page.locator("body").inner_text()
        m = RESULT_COUNT_RE.search(body)
        if not m:
            raise RuntimeError("Could not determine Eatigo Singapore result count")
        total = int(m.group(1).replace(",", ""))
        pages = math.ceil(total / 21)

        found: dict[str, dict] = {}
        previous_ids: set[str] = set()
        signatures: set[tuple[str, ...]] = set()
        for page_num in range(1, pages + 1):
            if page_num > 1:
                await click_page_number(page, page_num, previous_ids)
            rows = await anchors_for_page(page)
            ids = aggregate_anchors(rows, found)
            if not ids:
                raise RuntimeError(f"No Eatigo restaurant links found on result page {page_num}")
            signature = tuple(sorted(ids))
            if signature in signatures:
                raise RuntimeError(f"Eatigo pagination repeated a previous page at page {page_num}")
            signatures.add(signature)
            previous_ids = ids

        await browser.close()

    discovered = list(found.values())
    minimum = max(200, int(total * 0.90))
    if len(discovered) < minimum:
        raise RuntimeError(f"Eatigo crawl truncated: advertised={total}, discovered={len(discovered)}, minimum={minimum}")
    return discovered, total


def parse_branch_detail(html: str, fallback_name: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [clean(x) for x in soup.stripped_strings if clean(x)]
    name = fallback_name
    h1 = soup.find("h1")
    if h1 and clean(h1.get_text(" ", strip=True)):
        name = clean(h1.get_text(" ", strip=True))

    address = ""
    for i, line in enumerate(lines):
        if line.lower() == "copy address" and i:
            address = clean(lines[i - 1])
            break
    if not address:
        candidates = [x for x in lines if "singapore" in x.lower() and re.search(r"\b\d{6}\b", x)]
        if candidates:
            address = min(candidates, key=len)
    if not address or "singapore" not in address.lower():
        return None
    return {
        "name": name,
        "address": address,
        "postal_code": base.postal(address),
        "url": url,
        "address_verified": True,
    }


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def age_ok(value: str | None, max_age: timedelta) -> bool:
    try:
        dt = datetime.fromisoformat(value or "")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt <= max_age
    except Exception:
        return False


def fetch_branch_detail(item: dict) -> tuple[str, dict | None, bool, str | None]:
    last = None
    for attempt in range(2):
        try:
            r = requests.get(
                item["url"],
                headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"},
                timeout=20,
            )
            r.raise_for_status()
            detail = parse_branch_detail(r.text, item["name"], item["url"])
            if detail:
                return item["branch_id"], detail, False, None
            low = r.text.lower()
            explicit_non_sg = "malaysia" in low or "johor" in low
            return item["branch_id"], None, explicit_non_sg, None
        except Exception as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    return item["branch_id"], None, False, str(last)


def approximate_row(row: dict) -> dict:
    _, qualifier = split_listing_name(row.get("name") or "")
    label = clean(qualifier) or "Singapore"
    if "singapore" not in label.lower():
        label = f"{label}, Singapore"
    return {
        **row,
        "address": label,
        "postal_code": None,
        "address_verified": False,
    }


def add_branch_addresses(rows: list[dict]) -> list[dict]:
    """Best-effort address enrichment; never reduces the discovered list."""
    cache = read_json(BRANCH_CACHE, {})
    now = datetime.now(timezone.utc).isoformat()
    enriched: dict[str, dict] = {}
    need: list[dict] = []

    for row in rows:
        entry = cache.get(row["branch_id"], {})
        if age_ok(entry.get("fetched_at"), BRANCH_CACHE_MAX_AGE):
            if entry.get("non_singapore"):
                continue
            if entry.get("address"):
                enriched[row["branch_id"]] = {
                    **row,
                    **{k: entry.get(k) for k in ("name", "address", "postal_code", "url") if entry.get(k)},
                    "address_verified": True,
                }
                continue
        need.append(row)

    failures = 0
    non_sg = 0
    if need:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_branch_detail, row): row for row in need}
            for fut in as_completed(futures):
                row = futures[fut]
                bid, detail, is_non_sg, error = fut.result()
                if detail:
                    cache[bid] = {**detail, "fetched_at": now}
                    enriched[bid] = {**row, **detail}
                elif is_non_sg:
                    non_sg += 1
                    cache[bid] = {"non_singapore": True, "fetched_at": now}
                else:
                    failures += 1
                    stale = cache.get(bid, {})
                    if stale.get("address"):
                        enriched[bid] = {
                            **row,
                            **{k: stale.get(k) for k in ("name", "address", "postal_code", "url") if stale.get(k)},
                            "address_verified": True,
                        }

    DATA.mkdir(exist_ok=True)
    BRANCH_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    out = []
    for row in rows:
        if looks_non_singapore(row.get("name") or ""):
            continue
        entry = cache.get(row["branch_id"], {})
        if entry.get("non_singapore"):
            continue
        out.append(enriched.get(row["branch_id"]) or approximate_row(row))

    verified = sum(bool(x.get("address_verified")) for x in out)
    print(json.dumps({
        "eatigo_discovered_branches": len(rows),
        "eatigo_singapore_list": len(out),
        "eatigo_verified_addresses": verified,
        "eatigo_address_lookup_failures": failures,
        "eatigo_explicit_non_singapore": non_sg,
    }, indent=2))
    return out


def load_snapshot(allow_stale: bool = False) -> tuple[list[dict], int] | None:
    snap = read_json(SNAPSHOT, {})
    rows = snap.get("outlets") or []
    total = int(snap.get("advertised_total") or 0)
    if len(rows) < 200:
        return None
    if not allow_stale and not age_ok(snap.get("fetched_at"), SNAPSHOT_MAX_AGE):
        return None
    return rows, total or len(rows)


def save_snapshot(rows: list[dict], total: int) -> None:
    SNAPSHOT.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "advertised_total": total,
        "outlets": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def get_eatigo_outlets() -> tuple[list[dict], int, str]:
    fresh = load_snapshot(False)
    if fresh:
        return fresh[0], fresh[1], "fresh-cache"
    try:
        discovered, total = asyncio.run(discover_eatigo_browser())
        rows = add_branch_addresses(discovered)
        if len(rows) < 200:
            raise RuntimeError(f"Eatigo Singapore list unexpectedly small after filtering: {len(rows)}")
        save_snapshot(rows, total)
        return rows, total, "live-refresh"
    except Exception as exc:
        stale = load_snapshot(True)
        if stale:
            print(f"WARN Eatigo live refresh failed; using last good snapshot: {exc}")
            return stale[0], stale[1], "stale-cache"
        raise


def main() -> int:
    mp = DATA / "merchants.json"
    payload = json.loads(mp.read_text(encoding="utf-8"))
    merchants = payload.get("merchants", [])

    eatigo_outlets, advertised_total, source_mode = get_eatigo_outlets()

    for m in merchants:
        m.setdefault("eatigo", False)
        m.setdefault("eatigo_branch_id", None)
        m.setdefault("eatigo_url", None)
        m.setdefault("eatigo_location", None)
        m.setdefault("eatigo_match_note", None)

    used_merchants: set[int] = set()
    matched = 0
    for e in eatigo_outlets:
        head, location = split_listing_name(e["name"])
        best_i, note = choose_lc_match(e, merchants, used_merchants)
        if best_i is not None:
            m = merchants[best_i]
            used_merchants.add(best_i)
            m["eatigo"] = True
            m["eatigo_branch_id"] = e["branch_id"]
            m["eatigo_url"] = e["url"]
            m["eatigo_location"] = location or None
            m["eatigo_match_note"] = note
            matched += 1
            continue

        verified = bool(e.get("address_verified"))
        merchants.append({
            "name": e["name"],
            "brand": head,
            "address": e["address"],
            "postal_code": e.get("postal_code"),
            "category": "dining",
            "ld": False,
            "lc": False,
            "gha": False,
            "eatigo": True,
            "ld_source": None,
            "lc_section": None,
            "match_note": None,
            "gha_hotel": None,
            "gha_source": None,
            "gha_match_note": None,
            "gha_tiers": None,
            "eatigo_branch_id": e["branch_id"],
            "eatigo_url": e["url"],
            "eatigo_location": location or None,
            "eatigo_match_note": None,
            "eatigo_address_verified": verified,
            "geocode_skip": not verified,
            "id": base.make_id("Eatigo " + e["branch_id"], e["address"], e.get("postal_code")),
            "lat": None,
            "lng": None,
        })

    payload.setdefault("sources", {})["eatigo"] = SEARCH_URL
    payload.setdefault("stats", {})["eatigo"] = sum(bool(m.get("eatigo")) for m in merchants)
    payload["stats"]["eatigo_lc"] = sum(bool(m.get("eatigo")) and bool(m.get("lc")) for m in merchants)
    payload["eatigo_advertised_region_results"] = advertised_total
    payload["eatigo_source_mode"] = source_mode
    payload["merchants"] = sorted(
        merchants,
        key=lambda x: (str(x.get("name", "")).lower(), str(x.get("postal_code") or "")),
    )

    actual = [m for m in payload["merchants"] if m.get("eatigo")]
    if len(actual) < 200:
        raise RuntimeError(f"Eatigo merge produced only {len(actual)} Singapore outlets")

    mp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "eatigo_advertised_region_results": advertised_total,
        "eatigo_singapore_list": len(actual),
        "eatigo_verified_addresses": sum(bool(m.get("eatigo_address_verified")) or (m.get("eatigo") and m.get("lc")) for m in actual),
        "eatigo_lc": matched,
        "source_mode": source_mode,
        "time_slots_collected": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
