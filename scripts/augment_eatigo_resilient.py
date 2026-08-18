#!/usr/bin/env python3
"""Resilient Eatigo Singapore list import.

Goal:
- discover the complete Eatigo Singapore-region restaurant list across pagination;
- do not collect time slots or discount percentages;
- cache only branch addresses for mapping and strict Eatigo+LC matching;
- never block publication of a complete Eatigo list merely because some addresses
  could not be refreshed in the same run.
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
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from scripts import refresh_data as base

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEARCH_URL = "https://eatigo.com/en/regions/27/search"
BASE_URL = "https://eatigo.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151 Safari/537.36"

BRANCH_RE = re.compile(r"/branches/(\d+)")
RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)\s+results", re.I)
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
    return " ".join(t for t in key_text(value).split() if t not in GENERIC_NAME_WORDS)


def display_name_from_anchor(text: str) -> str:
    s = clean(text)
    s = RATING_TAIL_RE.sub("", s).strip()
    return re.sub(r"\s+NEW$", "", s, flags=re.I).strip()


def split_listing_name(value: str) -> tuple[str, str]:
    parts = re.split(r"\s+@\s+", clean(value), maxsplit=1)
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


def looks_non_singapore(value: str) -> bool:
    k = key_text(value)
    return any(h in k for h in NON_SG_HINTS)


def branch_url(href: str) -> str:
    u = urlparse(urljoin(BASE_URL, href))
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", "", ""))


def text_score(a: str, b: str) -> float:
    a, b = name_key(a), name_key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.94
    return SequenceMatcher(None, a, b).ratio()


def eatigo_name_score(e: dict, m: dict) -> float:
    head, _ = split_listing_name(e.get("name") or "")
    return max(text_score(head, m.get("name") or ""), text_score(head, m.get("brand") or ""))


def same_location(e: dict, m: dict) -> bool:
    ep, mp = e.get("postal_code"), m.get("postal_code")
    if ep and mp:
        return str(ep) == str(mp)
    ea = base.norm_address(e.get("address"))
    ma = base.norm_address(m.get("address"))
    if not ea or not ma:
        return False
    if ea in ma or ma in ea:
        return True
    return SequenceMatcher(None, ea, ma).ratio() >= 0.86


def choose_lc_match(e: dict, merchants: list[dict], used: set[int]) -> tuple[int | None, str | None]:
    candidates = []
    for i, m in enumerate(merchants):
        if i in used or not m.get("lc") or m.get("category") != "dining":
            continue
        if not same_location(e, m):
            continue
        score = eatigo_name_score(e, m)
        if score >= 0.78:
            candidates.append((i, score))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[1], reverse=True)
    if len(candidates) == 1 or candidates[0][1] - candidates[1][1] >= 0.08:
        i, score = candidates[0]
        return i, f"Eatigo+LC outlet+location match ({score:.2f})"
    return None, None


async def anchors_for_page(page) -> list[dict]:
    return await page.locator('a[href*="/branches/"]').evaluate_all(
        """els => els.map(a => ({
          href: a.href || a.getAttribute('href') || '',
          text: (a.innerText || a.textContent || '').trim()
        }))"""
    )


def aggregate_anchors(rows: list[dict], found: dict[str, dict]) -> set[str]:
    ids: set[str] = set()
    for row in rows:
        href = clean(row.get("href"))
        match = BRANCH_RE.search(href)
        if not match:
            continue
        bid = match.group(1)
        ids.add(bid)

        text = clean(row.get("text"))
        if not text or text.lower() in {"more", "view more", "today", "tomorrow"} or TIME_TEXT_RE.match(text):
            continue
        name = display_name_from_anchor(text)
        if not name:
            continue
        old = found.get(bid)
        if old is None or len(name) > len(old["name"]):
            found[bid] = {"branch_id": bid, "name": name, "url": branch_url(href)}
    return ids


async def result_range(page) -> tuple[int, int, int] | None:
    body = await page.locator("body").inner_text()
    m = RANGE_RE.search(body)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3).replace(",", ""))


async def click_number(page, target: int) -> bool:
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(200)
    return bool(await page.evaluate(
        """n => {
          const els = [...document.querySelectorAll('button,a,[role="button"]')]
            .filter(e => {
              if ((e.textContent || '').trim() !== String(n)) return false;
              const r = e.getBoundingClientRect();
              if (!r.width || !r.height) return false;
              const s = getComputedStyle(e);
              return s.visibility !== 'hidden' && s.display !== 'none';
            })
            .sort((a,b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
          if (!els.length) return false;
          els[0].scrollIntoView({block:'center'});
          els[0].click();
          return true;
        }""",
        target,
    ))


async def click_next_fallback(page) -> bool:
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(200)
    return bool(await page.evaluate(
        """() => {
          const visible = e => {
            const r=e.getBoundingClientRect(), s=getComputedStyle(e);
            return r.width && r.height && s.visibility !== 'hidden' && s.display !== 'none';
          };
          const candidates=[...document.querySelectorAll('button,a,[role="button"]')].filter(visible);
          const named=candidates.filter(e => {
            const a=((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('title')||'')+' '+(e.textContent||'')).toLowerCase();
            return /next|right|forward/.test(a);
          });
          if (named.length) {
            named.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
            named[0].click(); return true;
          }
          const nums=candidates.filter(e=>/^\d+$/.test((e.textContent||'').trim()));
          if (!nums.length) return false;
          const bottom=Math.max(...nums.map(e=>e.getBoundingClientRect().top));
          const row=candidates.filter(e=>Math.abs(e.getBoundingClientRect().top-bottom)<45);
          const maxNumRight=Math.max(...nums.map(e=>e.getBoundingClientRect().right));
          const right=row.filter(e=>{
            const r=e.getBoundingClientRect();
            const disabled=e.disabled || e.getAttribute('aria-disabled')==='true';
            return !disabled && r.left >= maxNumRight-4;
          }).sort((a,b)=>a.getBoundingClientRect().left-b.getBoundingClientRect().left);
          if (!right.length) return false;
          right[0].click(); return true;
        }"""
    ))


async def advance_page(page, target_page: int, previous_ids: set[str], previous_range) -> None:
    clicked = await click_number(page, target_page)
    if not clicked:
        clicked = await click_next_fallback(page)
    if not clicked:
        raise RuntimeError(f"Could not activate Eatigo pagination for page {target_page}")

    for _ in range(50):
        await page.wait_for_timeout(250)
        rr = await result_range(page)
        rows = await anchors_for_page(page)
        ids = {BRANCH_RE.search(x.get("href", "")).group(1)
               for x in rows if BRANCH_RE.search(x.get("href", ""))}
        if ids and ids != previous_ids and rr and rr != previous_range:
            return
    raise RuntimeError(f"Eatigo page {target_page} did not load a new result set")


async def discover_live() -> tuple[list[dict], int]:
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

        first_range = await result_range(page)
        if not first_range:
            raise RuntimeError("Could not determine Eatigo result count")
        total = first_range[2]
        pages = math.ceil(total / 21)

        found: dict[str, dict] = {}
        previous_ids: set[str] = set()
        previous_range = None
        signatures: set[tuple[str, ...]] = set()

        for page_num in range(1, pages + 1):
            if page_num > 1:
                await advance_page(page, page_num, previous_ids, previous_range)
            rr = await result_range(page)
            rows = await anchors_for_page(page)
            ids = aggregate_anchors(rows, found)
            if not ids:
                raise RuntimeError(f"No Eatigo branch links on result page {page_num}")
            sig = tuple(sorted(ids))
            if sig in signatures:
                raise RuntimeError(f"Eatigo pagination repeated a page at page {page_num}")
            signatures.add(sig)
            previous_ids = ids
            previous_range = rr
            print(f"Eatigo page {page_num}/{pages}: range={rr}, unique_branches={len(found)}")

        await browser.close()

    discovered = list(found.values())
    min_discovered = max(200, int(total * 0.88))
    if len(discovered) < min_discovered:
        raise RuntimeError(f"Eatigo discovery incomplete: {len(discovered)}/{total}; need at least {min_discovered}")
    return discovered, total


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


def load_snapshot() -> tuple[list[dict], int] | None:
    s = read_json(SNAPSHOT, {})
    rows = s.get("restaurants") or []
    total = int(s.get("advertised_total") or 0)
    if len(rows) >= 200 and total and age_ok(s.get("fetched_at"), SNAPSHOT_MAX_AGE):
        return rows, total
    return None


def save_snapshot(rows: list[dict], total: int) -> None:
    SNAPSHOT.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "advertised_total": total,
        "restaurants": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_branch_detail(html: str, fallback_name: str, url: str) -> tuple[dict | None, bool]:
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

    if address and "singapore" in address.lower():
        return {
            "name": name,
            "address": address,
            "postal_code": base.postal(address),
            "url": url,
        }, False

    low = " ".join(lines).lower()
    non_sg = any(x in low for x in ("johor", "malaysia"))
    return None, non_sg


def fetch_detail(item: dict) -> tuple[str, dict | None, bool, str | None]:
    last = None
    for attempt in range(5):
        try:
            r = requests.get(
                item["url"],
                headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"},
                timeout=30,
            )
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or (2 ** attempt))
                time.sleep(min(wait, 20))
                continue
            r.raise_for_status()
            detail, non_sg = parse_branch_detail(r.text, item["name"], item["url"])
            return item["branch_id"], detail, non_sg, None
        except Exception as exc:
            last = exc
            time.sleep(min(1.5 * (2 ** attempt), 12))
    return item["branch_id"], None, False, str(last)


def enrich_addresses(rows: list[dict]) -> tuple[dict[str, dict], set[str]]:
    cache = read_json(BRANCH_CACHE, {})
    now = datetime.now(timezone.utc).isoformat()
    verified: dict[str, dict] = {}
    non_sg: set[str] = set()
    need = []

    for row in rows:
        entry = cache.get(row["branch_id"], {})
        if age_ok(entry.get("fetched_at"), BRANCH_CACHE_MAX_AGE):
            if entry.get("non_singapore"):
                non_sg.add(row["branch_id"])
            elif entry.get("address"):
                verified[row["branch_id"]] = {**row, **{
                    k: entry.get(k) for k in ("name", "address", "postal_code", "url") if entry.get(k)
                }}
            else:
                need.append(row)
        else:
            need.append(row)

    failures = []
    if need:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(fetch_detail, row): row for row in need}
            for fut in as_completed(futures):
                row = futures[fut]
                bid, detail, is_non_sg, error = fut.result()
                if detail:
                    cache[bid] = {**detail, "fetched_at": now}
                    verified[bid] = {**row, **detail}
                elif is_non_sg:
                    cache[bid] = {"non_singapore": True, "fetched_at": now}
                    non_sg.add(bid)
                else:
                    failures.append((bid, error or "address unavailable"))

    BRANCH_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "eatigo_discovered": len(rows),
        "eatigo_verified_addresses": len(verified),
        "eatigo_non_singapore": len(non_sg),
        "eatigo_unresolved_addresses": len(failures),
    }, indent=2))
    return verified, non_sg


def approximate_address(name: str) -> str:
    _, location = split_listing_name(name)
    if location:
        return location if "singapore" in location.lower() else f"{location}, Singapore"
    return "Singapore"


def main() -> int:
    DATA.mkdir(exist_ok=True)
    mp = DATA / "merchants.json"
    payload = json.loads(mp.read_text(encoding="utf-8"))
    merchants = payload.get("merchants", [])

    source_mode = "live"
    try:
        discovered, total = asyncio.run(discover_live())
        save_snapshot(discovered, total)
    except Exception as exc:
        snap = load_snapshot()
        if not snap:
            raise
        discovered, total = snap
        source_mode = "cached_snapshot"
        print(f"WARN live Eatigo discovery failed; using last good snapshot: {exc}")

    verified, explicit_non_sg = enrich_addresses(discovered)

    for m in merchants:
        m.setdefault("eatigo", False)
        m.setdefault("eatigo_branch_id", None)
        m.setdefault("eatigo_url", None)
        m.setdefault("eatigo_location", None)
        m.setdefault("eatigo_match_note", None)

    used_lc: set[int] = set()
    matched = 0
    unresolved = 0

    for row in discovered:
        bid = row["branch_id"]
        if bid in explicit_non_sg or looks_non_singapore(row.get("name") or ""):
            continue

        e = verified.get(bid)
        is_verified = e is not None
        if not e:
            e = {
                **row,
                "address": approximate_address(row.get("name") or ""),
                "postal_code": None,
            }
            unresolved += 1

        head, location = split_listing_name(e.get("name") or row.get("name") or "")
        best_i = None
        note = None
        if is_verified:
            best_i, note = choose_lc_match(e, merchants, used_lc)

        if best_i is not None:
            m = merchants[best_i]
            used_lc.add(best_i)
            m["eatigo"] = True
            m["eatigo_branch_id"] = bid
            m["eatigo_url"] = row["url"]
            m["eatigo_location"] = location or None
            m["eatigo_match_note"] = note
            matched += 1
            continue

        merchants.append({
            "name": e.get("name") or row["name"],
            "brand": head or (e.get("name") or row["name"]),
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
            "eatigo_branch_id": bid,
            "eatigo_url": row["url"],
            "eatigo_location": location or None,
            "eatigo_match_note": None,
            "geocode_skip": not is_verified,
            "id": base.make_id("Eatigo " + bid, e.get("name") or row["name"], e.get("postal_code")),
            "lat": None,
            "lng": None,
        })

    payload.setdefault("sources", {})["eatigo"] = SEARCH_URL
    payload.setdefault("stats", {})["eatigo"] = sum(bool(m.get("eatigo")) for m in merchants)
    payload["stats"]["eatigo_lc"] = sum(bool(m.get("eatigo")) and bool(m.get("lc")) for m in merchants)
    payload["eatigo_advertised_region_results"] = total
    payload["eatigo_discovered_results"] = len(discovered)
    payload["eatigo_verified_addresses"] = len(verified)
    payload["eatigo_unresolved_addresses"] = unresolved
    payload["eatigo_source_mode"] = source_mode
    payload["merchants"] = sorted(
        merchants,
        key=lambda x: (str(x.get("name", "")).lower(), str(x.get("postal_code") or "")),
    )

    actual = [m for m in payload["merchants"] if m.get("eatigo")]
    minimum = max(200, int(total * 0.60))
    if len(actual) < minimum:
        raise RuntimeError(f"Eatigo list too small after Singapore filtering: {len(actual)}/{total}; minimum={minimum}")

    mp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "eatigo_region_advertised": total,
        "eatigo_discovered": len(discovered),
        "eatigo_singapore_list": len(actual),
        "eatigo_verified_addresses": len(verified),
        "eatigo_unresolved_addresses": unresolved,
        "eatigo_lc": matched,
        "source_mode": source_mode,
        "time_slots_collected": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
