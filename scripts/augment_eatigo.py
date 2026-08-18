#!/usr/bin/env python3
"""Add current Eatigo Singapore restaurants and time-based discounts to merchants.json.

Source of truth:
https://eatigo.com/en/regions/27/search

Eatigo is dynamic: restaurants and discounts vary by date/time. We crawl the
public Singapore search results with a headless browser because the result
pagination is client-rendered, retain the visible slot discounts, cache branch
addresses, then match Eatigo outlets to AMEX Lifestyle Credit at outlet +
location level.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from scripts import refresh_data as base

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEARCH_URL = "https://eatigo.com/en/regions/27/search"
BASE_URL = "https://eatigo.com"
UA = "Mozilla/5.0 (compatible; SingaporeDiningBenefitFinder/1.0)"
CACHE_PATH = DATA / "eatigo_branch_cache.json"
CACHE_MAX_AGE = timedelta(days=30)
SLOTS_PER_OUTLET = 36

TIME_DISCOUNT_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,3})\s*%\s*$", re.I)
BRANCH_RE = re.compile(r"/branches/(\d+)")
RESULT_COUNT_RE = re.compile(r"1\s*-\s*\d+\s+of\s+([\d,]+)\s+results", re.I)
RATING_TAIL_RE = re.compile(
    r"\s+\d(?:\.\d)?(?:\s+[\d.]+[kKmM]?\s+reservations)?(?:\s+(?:Hot|New))?\s*$",
    re.I,
)
GENERIC_NAME_WORDS = {
    "the", "and", "restaurant", "restaurants", "cafe", "café", "bar", "grill",
    "buffet", "kitchen", "dining",
}


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
    return s


def slot_from_href(text: str, href: str) -> dict | None:
    m = TIME_DISCOUNT_RE.match(clean(text))
    if not m:
        return None
    time_text, discount = m.group(1), int(m.group(2))
    parsed = urlparse(href)
    q = parse_qs(parsed.query)
    raw = q.get("slot", [""])[0]
    raw = unquote(raw).replace("+", " ")
    date = raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", raw) else None
    return {"date": date, "time": time_text, "discount": discount}


def extract_branch_detail(html: bytes | str, fallback_name: str, url: str) -> dict | None:
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
            if address:
                break

    if not address or "singapore" not in address.lower():
        return None

    return {
        "name": name,
        "address": address,
        "postal_code": base.postal(address),
        "url": url.split("?", 1)[0],
    }


def same_location(eatigo: dict, merchant: dict) -> bool:
    ep, mp = eatigo.get("postal_code"), merchant.get("postal_code")
    if ep and mp:
        return ep == mp
    ea = base.norm_address(eatigo.get("address"))
    ma = base.norm_address(merchant.get("address"))
    if ea and ma and (ea in ma or ma in ea):
        return True
    return SequenceMatcher(None, ea, ma).ratio() >= 0.82 if ea and ma else False


def name_score(eatigo: dict, merchant: dict) -> float:
    e = name_key(eatigo.get("name"))
    candidates = {name_key(merchant.get("name")), name_key(merchant.get("brand"))} - {""}
    best = 0.0
    for c in candidates:
        if e == c:
            return 1.0
        if e and c and (e in c or c in e):
            best = max(best, 0.94)
        best = max(best, SequenceMatcher(None, e, c).ratio())

    head = name_key(str(eatigo.get("name") or "").split("@", 1)[0])
    for c in candidates:
        if head == c:
            return 1.0
        if head and c and (head in c or c in head):
            best = max(best, 0.96)
        best = max(best, SequenceMatcher(None, head, c).ratio())
    return best


async def anchors_for_page(page) -> list[dict]:
    return await page.locator('a[href*="/branches/"]').evaluate_all(
        """els => els.map(a => ({
            href: a.href || a.getAttribute('href') || '',
            text: (a.innerText || a.textContent || '').trim()
        }))"""
    )


def aggregate_anchors(rows: list[dict], found: dict[str, dict]) -> None:
    for row in rows:
        href = clean(row.get("href"))
        text = clean(row.get("text"))
        m = BRANCH_RE.search(href)
        if not m:
            continue
        bid = m.group(1)
        item = found.setdefault(
            bid,
            {"branch_id": bid, "name": "", "url": urljoin(BASE_URL, href).split("?", 1)[0], "slots": []},
        )

        slot = slot_from_href(text, href)
        if slot:
            k = (slot.get("date"), slot["time"], slot["discount"])
            existing = {(x.get("date"), x["time"], x["discount"]) for x in item["slots"]}
            if k not in existing and len(item["slots"]) < SLOTS_PER_OUTLET:
                item["slots"].append(slot)
            continue

        if text.lower() in {"more", "view more"} or not text or len(text) < 3:
            continue
        candidate = display_name_from_anchor(text)
        if candidate and not TIME_DISCOUNT_RE.match(candidate) and candidate.lower() not in {"today", "tomorrow"}:
            if not item["name"] or len(candidate) > len(item["name"]):
                item["name"] = candidate


async def click_page_number(page, page_num: int, previous_ids: set[str]) -> None:
    locator = page.get_by_text(str(page_num), exact=True)
    count = await locator.count()
    clicked = False
    for i in range(count - 1, -1, -1):
        el = locator.nth(i)
        try:
            if not await el.is_visible():
                continue
            await el.click(timeout=5000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        raise RuntimeError(f"Could not click Eatigo pagination page {page_num}")

    for _ in range(20):
        await page.wait_for_timeout(350)
        rows = await anchors_for_page(page)
        ids = {BRANCH_RE.search(x["href"]).group(1) for x in rows if BRANCH_RE.search(x.get("href", ""))}
        if ids and ids != previous_ids:
            return
    raise RuntimeError(f"Eatigo pagination page {page_num} did not load new restaurant results")


async def discover_eatigo() -> tuple[list[dict], int]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        context = await browser.new_context(user_agent=UA, locale="en-SG")
        page = await context.new_page()
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector('a[href*="/branches/"]', timeout=30000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("Eatigo search page did not expose restaurant links") from exc
        await page.wait_for_timeout(1500)

        body = await page.locator("body").inner_text()
        m = RESULT_COUNT_RE.search(body)
        if not m:
            raise RuntimeError("Could not determine Eatigo Singapore result count")
        total = int(m.group(1).replace(",", ""))
        pages = math.ceil(total / 21)

        found: dict[str, dict] = {}
        previous_ids: set[str] = set()
        for page_num in range(1, pages + 1):
            if page_num > 1:
                await click_page_number(page, page_num, previous_ids)
            rows = await anchors_for_page(page)
            ids = {BRANCH_RE.search(x["href"]).group(1) for x in rows if BRANCH_RE.search(x.get("href", ""))}
            if not ids:
                raise RuntimeError(f"No restaurant links found on Eatigo result page {page_num}")
            previous_ids = ids
            aggregate_anchors(rows, found)

        await browser.close()

    if total >= 100 and len(found) < total * 0.80:
        raise RuntimeError(f"Eatigo crawl appears truncated: advertised={total}, discovered={len(found)}")
    return list(found.values()), total


def load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cache_is_fresh(entry: dict) -> bool:
    try:
        dt = datetime.fromisoformat(entry.get("fetched_at", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt <= CACHE_MAX_AGE and bool(entry.get("address"))
    except Exception:
        return False


def fetch_detail(item: dict) -> tuple[str, dict | None]:
    bid = item["branch_id"]
    r = requests.get(
        item["url"],
        headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"},
        timeout=35,
    )
    r.raise_for_status()
    return bid, extract_branch_detail(r.content, item.get("name") or f"Eatigo {bid}", item["url"])


def hydrate_branch_details(items: list[dict]) -> list[dict]:
    DATA.mkdir(exist_ok=True)
    cache = load_cache()
    now = datetime.now(timezone.utc).isoformat()

    need_fetch = [x for x in items if not cache_is_fresh(cache.get(x["branch_id"], {}))]
    if need_fetch:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(fetch_detail, x): x for x in need_fetch}
            for fut in as_completed(futures):
                item = futures[fut]
                bid = item["branch_id"]
                try:
                    _, detail = fut.result()
                    if detail:
                        cache[bid] = {**detail, "fetched_at": now}
                    else:
                        cache[bid] = {"non_singapore": True, "fetched_at": now}
                except Exception as exc:
                    print(f"WARN Eatigo branch {bid}: {exc}")

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    out = []
    for item in items:
        detail = cache.get(item["branch_id"], {})
        if detail.get("non_singapore") or not detail.get("address"):
            continue
        slots = sorted(
            item.get("slots", []),
            key=lambda x: ((x.get("date") or "9999-99-99"), x.get("time") or "99:99", -int(x.get("discount") or 0)),
        )
        out.append(
            {
                **detail,
                "branch_id": item["branch_id"],
                "name": detail.get("name") or item.get("name") or f"Eatigo {item['branch_id']}",
                "url": item["url"],
                "slots": slots[:SLOTS_PER_OUTLET],
                "max_discount": max((int(x.get("discount") or 0) for x in slots), default=None),
            }
        )
    return out


def main() -> int:
    mp = DATA / "merchants.json"
    payload = json.loads(mp.read_text(encoding="utf-8"))
    merchants = payload.get("merchants", [])

    discovered, advertised_total = asyncio.run(discover_eatigo())
    eatigo_outlets = hydrate_branch_details(discovered)
    if len(eatigo_outlets) < 100:
        raise RuntimeError(
            f"Unexpectedly small Singapore Eatigo list after address filtering: {len(eatigo_outlets)} "
            f"(advertised region results {advertised_total})"
        )

    for m in merchants:
        m.setdefault("eatigo", False)
        m.setdefault("eatigo_branch_id", None)
        m.setdefault("eatigo_url", None)
        m.setdefault("eatigo_slots", None)
        m.setdefault("eatigo_max_discount", None)
        m.setdefault("eatigo_match_note", None)

    used_merchants: set[int] = set()
    matched = 0
    for e in eatigo_outlets:
        best_i, best_score = None, 0.0
        for i, m in enumerate(merchants):
            if i in used_merchants or not m.get("lc") or m.get("category") != "dining":
                continue
            if not same_location(e, m):
                continue
            score = name_score(e, m)
            if score > best_score:
                best_i, best_score = i, score

        if best_i is not None and best_score >= 0.72:
            m = merchants[best_i]
            used_merchants.add(best_i)
            m["eatigo"] = True
            m["eatigo_branch_id"] = e["branch_id"]
            m["eatigo_url"] = e["url"]
            m["eatigo_slots"] = e["slots"]
            m["eatigo_max_discount"] = e["max_discount"]
            m["eatigo_match_note"] = f"Eatigo+LC outlet+location match ({best_score:.2f})"
            matched += 1
        else:
            merchants.append(
                {
                    "name": e["name"],
                    "brand": e["name"],
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
                    "eatigo_slots": e["slots"],
                    "eatigo_max_discount": e["max_discount"],
                    "eatigo_match_note": None,
                    "id": base.make_id("Eatigo " + e["name"], e["address"], e.get("postal_code")),
                    "lat": None,
                    "lng": None,
                }
            )

    payload.setdefault("sources", {})["eatigo"] = SEARCH_URL
    payload.setdefault("stats", {})["eatigo"] = sum(bool(m.get("eatigo")) for m in merchants)
    payload["stats"]["eatigo_lc"] = sum(bool(m.get("eatigo")) and bool(m.get("lc")) for m in merchants)
    payload["eatigo_advertised_region_results"] = advertised_total
    payload["merchants"] = sorted(
        merchants,
        key=lambda x: (
            str(x.get("name", "")).lower(),
            str(x.get("postal_code") or ""),
            str(x.get("address", "")).lower(),
        ),
    )

    actual = [m for m in payload["merchants"] if m.get("eatigo")]
    if len(actual) != len(eatigo_outlets):
        raise RuntimeError(
            f"Eatigo merge lost or duplicated outlets: source={len(eatigo_outlets)} merged={len(actual)}"
        )

    mp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "eatigo_region_advertised": advertised_total,
                "eatigo_singapore": len(eatigo_outlets),
                "eatigo_lc": matched,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
