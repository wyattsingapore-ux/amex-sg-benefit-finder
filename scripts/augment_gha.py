#!/usr/bin/env python3
"""Add Singapore Pan Pacific DISCOVERY (GHA) dining outlets to merchants.json.

Source of truth:
https://www.panpacific.com/en/dining/pphg-fb.html

The page explicitly lists Pan Pacific Hotels Group operated restaurants and bars
that participate in Pan Pacific DISCOVERY dining benefits. We only ingest the
Singapore section, then match each outlet to the existing AMEX Lifestyle Credit
records at outlet + location level.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from scripts import refresh_data as base

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
GHA_URL = "https://www.panpacific.com/en/dining/pphg-fb.html"
UA = "Mozilla/5.0 (compatible; AmexSGBenefitFinder/1.0)"

HOTELS = {
    "pan pacific orchard": ("Pan Pacific Orchard", "10 Claymore Road, Singapore 229540"),
    "pan pacific singapore": ("Pan Pacific Singapore", "7 Raffles Boulevard, Singapore 039595"),
    "parkroyal collection marina bay": ("PARKROYAL COLLECTION Marina Bay", "6 Raffles Boulevard, Singapore 039594"),
    "parkroyal collection pickering": ("PARKROYAL COLLECTION Pickering", "3 Upper Pickering Street, Singapore 058289"),
    "parkroyal on beach road": ("PARKROYAL on Beach Road", "7500 Beach Road, Singapore 199591"),
    "top of uob plaza": ("TOP of UOB Plaza", "80 Raffles Place, #60-01 UOB Plaza 1, Singapore 048624"),
}

GHA_TIERS = {"silver": 10, "gold": 15, "platinum": 20, "titanium": 25}
GENERIC_NAME_WORDS = {"the", "and", "restaurant", "restaurants", "bar", "bars", "lounge", "cafe", "café"}


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


def canonical_hotel(line: str):
    return HOTELS.get(key_text(line))


def parse_gha_singapore(html: bytes | str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [clean(x) for x in soup.stripped_strings if clean(x)]

    # The site contains other country selectors/navigation. Pick the SINGAPORE
    # block that is actually followed by one of the known participating hotel
    # headings and terminates at THAILAND.
    section: list[str] | None = None
    for start in [i for i, x in enumerate(lines) if x == "SINGAPORE"]:
        end = next((i for i in range(start + 1, len(lines)) if lines[i] == "THAILAND"), None)
        if end is None:
            continue
        candidate = lines[start + 1:end]
        if any(canonical_hotel(x) for x in candidate):
            section = candidate
            break
    if section is None:
        raise RuntimeError("Could not identify the Singapore participating-restaurant section")

    current_hotel: tuple[str, str] | None = None
    outlets: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for raw in section:
        line = clean(raw)
        if not line or line == "|":
            continue
        h = canonical_hotel(line)
        if h:
            current_hotel = h
            continue
        if current_hotel is None:
            continue

        hotel, address = current_hotel
        pc = base.postal(address)
        key = (name_key(line), pc or "")
        if not key[0] or key in seen:
            continue
        seen.add(key)
        outlets.append({"name": line, "hotel": hotel, "address": address, "postal_code": pc})

    if not 10 <= len(outlets) <= 40:
        raise RuntimeError(f"Unexpected Singapore GHA outlet count: {len(outlets)}")
    return outlets


def same_location(gha: dict, merchant: dict) -> bool:
    gp, mp = gha.get("postal_code"), merchant.get("postal_code")
    if gp and mp:
        return gp == mp
    ga = base.norm_address(gha.get("address"))
    ma = base.norm_address(merchant.get("address"))
    if ga and ma and (ga in ma or ma in ga):
        return True
    return SequenceMatcher(None, ga, ma).ratio() >= 0.82 if ga and ma else False


def name_score(gha: dict, merchant: dict) -> float:
    g = name_key(gha.get("name"))
    candidates = {name_key(merchant.get("name")), name_key(merchant.get("brand"))} - {""}
    best = 0.0
    for c in candidates:
        if g == c:
            return 1.0
        if g and c and (g in c or c in g):
            best = max(best, 0.94)
        best = max(best, SequenceMatcher(None, g, c).ratio())
    return best


def main() -> int:
    mp = DATA / "merchants.json"
    payload = json.loads(mp.read_text(encoding="utf-8"))
    merchants = payload.get("merchants", [])

    r = requests.get(GHA_URL, headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"}, timeout=60)
    r.raise_for_status()
    gha_outlets = parse_gha_singapore(r.content)

    for m in merchants:
        m.setdefault("gha", False)
        m.setdefault("gha_hotel", None)
        m.setdefault("gha_source", None)
        m.setdefault("gha_match_note", None)
        m.setdefault("gha_tiers", None)

    used_merchants: set[int] = set()
    matched = 0
    for g in gha_outlets:
        best_i, best_score = None, 0.0
        for i, m in enumerate(merchants):
            if i in used_merchants or not m.get("lc") or m.get("category") != "dining":
                continue
            if not same_location(g, m):
                continue
            score = name_score(g, m)
            if score > best_score:
                best_i, best_score = i, score

        if best_i is not None and best_score >= 0.72:
            m = merchants[best_i]
            used_merchants.add(best_i)
            m["gha"] = True
            m["gha_hotel"] = g["hotel"]
            m["gha_source"] = GHA_URL
            m["gha_match_note"] = f"GHA+LC outlet+location match ({best_score:.2f})"
            m["gha_tiers"] = GHA_TIERS
            matched += 1
        else:
            merchants.append({
                "name": g["name"], "brand": g["hotel"], "address": g["address"],
                "postal_code": g["postal_code"], "category": "dining",
                "ld": False, "lc": False, "gha": True,
                "ld_source": None, "lc_section": None, "match_note": None,
                "gha_hotel": g["hotel"], "gha_source": GHA_URL,
                "gha_match_note": None, "gha_tiers": GHA_TIERS,
                "id": base.make_id("GHA " + g["name"], g["address"], g["postal_code"]),
                "lat": None, "lng": None,
            })

    payload.setdefault("sources", {})["gha_dining"] = GHA_URL
    payload.setdefault("stats", {})["gha"] = sum(bool(m.get("gha")) for m in merchants)
    payload["stats"]["gha_lc"] = sum(bool(m.get("gha")) and bool(m.get("lc")) for m in merchants)
    payload["merchants"] = sorted(
        merchants,
        key=lambda x: (str(x.get("name", "")).lower(), str(x.get("postal_code") or ""), str(x.get("address", "")).lower()),
    )

    actual_gha = [m for m in payload["merchants"] if m.get("gha")]
    if len(actual_gha) != len(gha_outlets):
        raise RuntimeError(f"GHA merge lost or duplicated outlets: source={len(gha_outlets)} merged={len(actual_gha)}")

    mp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"gha": len(gha_outlets), "gha_lc": matched}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
