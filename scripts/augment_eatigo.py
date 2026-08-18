#!/usr/bin/env python3
"""Add a simple Eatigo Singapore restaurant list to merchants.json.

This intentionally does NOT collect time slots or discount percentages. We only
collect restaurant/listing membership, the branch's public street address, an
Eatigo link, and a strict Eatigo+LC outlet/location intersection.

The listing crawl uses ordinary HTTP requests. For each discovered branch we
read only the public address from the Eatigo branch page so the map can pin it.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

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
MAX_LISTING_PAGES = 120


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
    ep, mp = eatigo.get("postal_code"), merchant.get("postal_code")
    if ep and mp:
        return str(ep) == str(mp)
    ea = base.norm_address(eatigo.get("address"))
    ma = base.norm_address(merchant.get("address"))
    if ea and ma and (ea in ma or ma in ea):
        return True
    return SequenceMatcher(None, ea, ma).ratio() >= 0.86 if ea and ma else False


def choose_lc_match(eatigo: dict, merchants: list[dict], used: set[int]) -> tuple[int | None, str | None]:
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


def canonical_url(href: str) -> str:
    u = urlparse(urljoin(BASE_URL, href))
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", u.query, ""))


def branch_url(href: str) -> str:
    u = urlparse(urljoin(BASE_URL, href))
    return urlunparse((u.scheme, u.netloc, u.path.rstrip("/"), "", "", ""))


def is_region_listing(url: str) -> bool:
    u = urlparse(url)
    if u.netloc not in {"eatigo.com", "www.eatigo.com"}:
        return False
    p = u.path.rstrip("/")
    if not p.startswith("/en/regions/27") or "/branches/" in p:
        return False
    return p == "/en/regions/27" or p == "/en/regions/27/search" or any(
        token in p for token in ("/categories/", "/themes/", "/tags/")
    )


def parse_listing(html: str, page_url: str) -> tuple[list[dict], list[str], int | None]:
    soup = BeautifulSoup(html, "html.parser")
    restaurants: dict[str, dict] = {}
    listing_urls: set[str] = set()

    body_text = clean(soup.get_text(" ", strip=True))
    count_match = RESULT_COUNT_RE.search(body_text)
    advertised = int(count_match.group(1).replace(",", "")) if count_match else None

    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        full = canonical_url(href)
        bm = BRANCH_RE.search(full)
        if bm:
            text = clean(a.get_text(" ", strip=True))
            if not text or text.lower() in {"more", "view more", "today", "tomorrow"} or TIME_TEXT_RE.match(text):
                continue
            name = display_name_from_anchor(text)
            if not name or looks_non_singapore(name):
                continue
            bid = bm.group(1)
            current = restaurants.get(bid)
            if current is None or len(name) > len(current["name"]):
                restaurants[bid] = {"branch_id": bid, "name": name, "url": branch_url(href)}
        elif is_region_listing(full) and full != canonical_url(page_url):
            listing_urls.add(full)

    return list(restaurants.values()), sorted(listing_urls), advertised


def get(session: requests.Session, url: str) -> str:
    last = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Eatigo request failed for {url}: {last}")


def discover_eatigo() -> tuple[list[dict], int, int | None]:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"})
    seeds = [
        SEARCH_URL,
        SEARCH_URL + '?filter={"sortby":"popular"}',
        SEARCH_URL + '?filter={"sortby":"rating"}',
        SEARCH_URL + '?filter={"sortby":"discount"}',
        "https://eatigo.com/en/regions/27/themes/14764",
        "https://eatigo.com/en/regions/27/categories/14764",
        "https://eatigo.com/en/regions/27/themes/14758",
        "https://eatigo.com/en/regions/27/themes/14767",
        "https://eatigo.com/en/regions/27/themes/15814",
    ]
    q = deque(canonical_url(x) for x in seeds)
    seen_pages: set[str] = set()
    found: dict[str, dict] = {}
    failed_pages = 0
    advertised_total = None

    while q and len(seen_pages) < MAX_LISTING_PAGES:
        url = q.popleft()
        if url in seen_pages:
            continue
        seen_pages.add(url)
        try:
            html = get(session, url)
            rows, more, advertised = parse_listing(html, url)
            if advertised_total is None and advertised:
                advertised_total = advertised
        except Exception as exc:
            print(f"WARN Eatigo listing page {url}: {exc}")
            failed_pages += 1
            continue
        for row in rows:
            old = found.get(row["branch_id"])
            if old is None or len(row["name"]) > len(old["name"]):
                found[row["branch_id"]] = row
        for nxt in more:
            if nxt not in seen_pages:
                q.append(nxt)
        time.sleep(0.05)

    rows = [x for x in found.values() if x.get("name") and not looks_non_singapore(x["name"])]
    if len(rows) < 20:
        raise RuntimeError(f"Eatigo public-list crawl returned only {len(rows)} Singapore restaurants")
    print(json.dumps({
        "eatigo_listing_pages_checked": len(seen_pages),
        "eatigo_listing_pages_failed": failed_pages,
        "eatigo_unique_restaurants": len(rows),
        "eatigo_advertised_region_results": advertised_total,
    }, indent=2))
    return rows, len(seen_pages), advertised_total


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
        # Fallback for layout changes: prefer a concise line containing Singapore + postal code.
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
    }


def fetch_branch_detail(item: dict) -> tuple[str, dict | None, str | None]:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(
                item["url"],
                headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"},
                timeout=30,
            )
            r.raise_for_status()
            return item["branch_id"], parse_branch_detail(r.text, item["name"], item["url"]), None
        except Exception as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    return item["branch_id"], None, str(last)


def add_branch_addresses(rows: list[dict]) -> list[dict]:
    details: dict[str, dict] = {}
    failures = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_branch_detail, row): row for row in rows}
        for fut in as_completed(futures):
            item = futures[fut]
            bid, detail, error = fut.result()
            if detail:
                details[bid] = {**item, **detail}
            elif error:
                failures.append((bid, error))

    enriched = [details[x["branch_id"]] for x in rows if x["branch_id"] in details]
    success_rate = len(enriched) / max(1, len(rows))
    print(json.dumps({
        "eatigo_branch_addresses": len(enriched),
        "eatigo_branch_address_failures": len(rows) - len(enriched),
        "eatigo_address_success_rate": round(success_rate, 3),
    }, indent=2))
    if success_rate < 0.70:
        sample = failures[:3]
        raise RuntimeError(f"Eatigo address lookup succeeded for only {len(enriched)}/{len(rows)} branches; sample failures={sample}")
    return enriched


def main() -> int:
    mp = DATA / "merchants.json"
    payload = json.loads(mp.read_text(encoding="utf-8"))
    merchants = payload.get("merchants", [])

    discovered, pages_checked, advertised_total = discover_eatigo()
    eatigo_outlets = add_branch_addresses(discovered)

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

        merchants.append({
            "name": e["name"], "brand": head, "address": e["address"], "postal_code": e.get("postal_code"),
            "category": "dining", "ld": False, "lc": False, "gha": False, "eatigo": True,
            "ld_source": None, "lc_section": None, "match_note": None,
            "gha_hotel": None, "gha_source": None, "gha_match_note": None, "gha_tiers": None,
            "eatigo_branch_id": e["branch_id"], "eatigo_url": e["url"],
            "eatigo_location": location or None, "eatigo_match_note": None,
            "id": base.make_id("Eatigo " + e["branch_id"], e["address"], e.get("postal_code")),
            "lat": None, "lng": None,
        })

    payload.setdefault("sources", {})["eatigo"] = SEARCH_URL
    payload.setdefault("stats", {})["eatigo"] = sum(bool(m.get("eatigo")) for m in merchants)
    payload["stats"]["eatigo_lc"] = sum(bool(m.get("eatigo")) and bool(m.get("lc")) for m in merchants)
    payload["eatigo_listing_pages_checked"] = pages_checked
    payload["eatigo_advertised_region_results"] = advertised_total
    payload["merchants"] = sorted(merchants, key=lambda x: (str(x.get("name", "")).lower(), str(x.get("postal_code") or "")))

    actual = [m for m in payload["merchants"] if m.get("eatigo")]
    if len(actual) != len(eatigo_outlets):
        raise RuntimeError(f"Eatigo merge lost/duplicated outlets: source={len(eatigo_outlets)} merged={len(actual)}")

    mp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "eatigo_list": len(eatigo_outlets),
        "eatigo_lc": matched,
        "time_slots_collected": False,
        "discounts_collected": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
