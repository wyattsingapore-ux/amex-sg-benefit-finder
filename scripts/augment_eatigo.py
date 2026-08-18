#!/usr/bin/env python3
"""Add a simple Eatigo Singapore restaurant list to merchants.json.

This intentionally does NOT collect time slots or discount percentages. The user
only needs to know whether a restaurant is listed on Eatigo, plus an Eatigo link,
and whether the same outlet is also on the AMEX Lifestyle Credit list.

Source:
https://eatigo.com/en/regions/27/search
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from scripts import refresh_data as base

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEARCH_URL = "https://eatigo.com/en/regions/27/search"
BASE_URL = "https://eatigo.com"
UA = "Mozilla/5.0 (compatible; SingaporeDiningBenefitFinder/1.0)"

BRANCH_RE = re.compile(r"/branches/(\d+)")
RESULT_COUNT_RE = re.compile(r"1\s*-\s*\d+\s+of\s+([\d,]+)\s+results", re.I)
TIME_TEXT_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s*-\s*\d{1,3}\s*%\s*$", re.I)
RATING_TAIL_RE = re.compile(
    r"\s+(?:NEW\s+)?\d(?:\.\d)?(?:\s+[\d.]+[kKmM]?\s+reservations)?(?:\s+(?:Hot|New))?\s*$",
    re.I,
)
GENERIC_NAME_WORDS = {
    "the", "and", "restaurant", "restaurants", "cafe", "café", "bar", "grill",
    "buffet", "kitchen", "dining",
}
# Eatigo's Singapore-region search can surface a few nearby Johor listings.
# These obvious location hints are excluded without opening hundreds of detail pages.
NON_SG_HINTS = (
    "johor", "tebrau", "southkey", "iskandar puteri", "mount austin", "komtar jbcc",
    "paradigm mall jb", "aeon mall", "sutera mall", "city square jb",
)


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
    return max(
        text_score(head, merchant.get("name") or ""),
        text_score(head, merchant.get("brand") or ""),
    )


def qualifier_score(eatigo: dict, merchant: dict) -> float:
    _, qualifier = split_listing_name(eatigo.get("name") or "")
    if not qualifier:
        return 0.0
    hay = " ".join(
        str(x or "") for x in [merchant.get("brand"), merchant.get("name"), merchant.get("address")]
    )
    q, h = key_text(qualifier), key_text(hay)
    if q and q in h:
        return 1.0
    q_tokens = set(q.split())
    h_tokens = set(h.split())
    overlap = len(q_tokens & h_tokens) / max(1, len(q_tokens))
    return max(overlap, SequenceMatcher(None, q, h).ratio() if q and h else 0.0)


def choose_lc_match(eatigo: dict, merchants: list[dict], used: set[int]) -> tuple[int | None, str | None]:
    candidates = []
    for i, m in enumerate(merchants):
        if i in used or not m.get("lc") or m.get("category") != "dining":
            continue
        hs = head_score(eatigo, m)
        if hs < 0.88:
            continue
        qs = qualifier_score(eatigo, m)
        candidates.append((i, hs, qs))

    if not candidates:
        return None, None

    # Strong branch/property evidence wins.
    qualified = sorted((x for x in candidates if x[2] >= 0.45), key=lambda x: (x[2], x[1]), reverse=True)
    if qualified:
        best = qualified[0]
        if len(qualified) == 1 or best[2] - qualified[1][2] >= 0.12:
            return best[0], f"Eatigo+LC name+branch match (name={best[1]:.2f}, branch={best[2]:.2f})"

    # If only one LC dining outlet has this strong restaurant name, accepting it
    # is safer than guessing between multiple branches of the same brand.
    strong = [x for x in candidates if x[1] >= 0.95]
    if len(strong) == 1:
        return strong[0][0], f"Eatigo+LC unique outlet-name match ({strong[0][1]:.2f})"
    return None, None


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
            {"branch_id": bid, "name": "", "url": urljoin(BASE_URL, href).split("?", 1)[0]},
        )
        if not text or text.lower() in {"more", "view more", "today", "tomorrow"} or TIME_TEXT_RE.match(text):
            continue
        candidate = display_name_from_anchor(text)
        if candidate and (not item["name"] or len(candidate) > len(item["name"])):
            item["name"] = candidate


async def click_page_number(page, page_num: int, previous_ids: set[str]) -> None:
    # Pagination is client-rendered; sequential navigation keeps the next page
    # number visible as the pagination window advances.
    locator = page.get_by_text(str(page_num), exact=True)
    count = await locator.count()
    clicked = False
    for i in range(count - 1, -1, -1):
        el = locator.nth(i)
        try:
            if await el.is_visible():
                await el.click(timeout=6000)
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        raise RuntimeError(f"Could not click Eatigo pagination page {page_num}")

    for _ in range(24):
        await page.wait_for_timeout(300)
        rows = await anchors_for_page(page)
        ids = {BRANCH_RE.search(x.get("href", "")).group(1) for x in rows if BRANCH_RE.search(x.get("href", ""))}
        if ids and ids != previous_ids:
            return
    raise RuntimeError(f"Eatigo pagination page {page_num} did not load new results")


async def discover_eatigo() -> tuple[list[dict], int]:
    async with async_playwright() as p:
        launch = {"headless": True, "args": ["--disable-dev-shm-usage", "--no-sandbox"]}
        system_chrome = (
            shutil.which("google-chrome") or shutil.which("google-chrome-stable") or
            shutil.which("chromium") or shutil.which("chromium-browser")
        )
        if system_chrome:
            launch["executable_path"] = system_chrome
        browser = await p.chromium.launch(**launch)
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
            raise RuntimeError("Could not determine Eatigo result count")
        total = int(m.group(1).replace(",", ""))
        pages = math.ceil(total / 21)

        found: dict[str, dict] = {}
        previous_ids: set[str] = set()
        for page_num in range(1, pages + 1):
            if page_num > 1:
                await click_page_number(page, page_num, previous_ids)
            rows = await anchors_for_page(page)
            ids = {BRANCH_RE.search(x.get("href", "")).group(1) for x in rows if BRANCH_RE.search(x.get("href", ""))}
            if not ids:
                raise RuntimeError(f"No restaurant links on Eatigo result page {page_num}")
            previous_ids = ids
            aggregate_anchors(rows, found)

        await browser.close()

    rows = [x for x in found.values() if x.get("name") and not looks_non_singapore(x["name"])]
    if total >= 100 and len(found) < total * 0.75:
        raise RuntimeError(f"Eatigo crawl appears truncated: advertised={total}, discovered={len(found)}")
    if len(rows) < 100:
        raise RuntimeError(f"Unexpectedly small Eatigo Singapore list after filtering: {len(rows)}")
    return rows, total


def main() -> int:
    mp = DATA / "merchants.json"
    payload = json.loads(mp.read_text(encoding="utf-8"))
    merchants = payload.get("merchants", [])

    eatigo_outlets, advertised_total = asyncio.run(discover_eatigo())

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

        display_location = location or "Singapore"
        approx_address = f"{display_location}, Singapore" if "singapore" not in display_location.lower() else display_location
        merchants.append({
            "name": e["name"],
            "brand": head,
            "address": approx_address,
            "postal_code": None,
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
            "geocode_skip": True,
            "id": base.make_id("Eatigo " + e["branch_id"], e["name"], None),
            "lat": None,
            "lng": None,
        })

    payload.setdefault("sources", {})["eatigo"] = SEARCH_URL
    payload.setdefault("stats", {})["eatigo"] = sum(bool(m.get("eatigo")) for m in merchants)
    payload["stats"]["eatigo_lc"] = sum(bool(m.get("eatigo")) and bool(m.get("lc")) for m in merchants)
    payload["eatigo_advertised_region_results"] = advertised_total
    payload["merchants"] = sorted(
        merchants,
        key=lambda x: (str(x.get("name", "")).lower(), str(x.get("postal_code") or "")),
    )

    actual = [m for m in payload["merchants"] if m.get("eatigo")]
    if len(actual) != len(eatigo_outlets):
        raise RuntimeError(f"Eatigo merge lost/duplicated outlets: source={len(eatigo_outlets)} merged={len(actual)}")

    mp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "eatigo_region_advertised": advertised_total,
        "eatigo_list": len(eatigo_outlets),
        "eatigo_lc": matched,
        "time_slots_collected": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
