#!/usr/bin/env python3
"""Refresh AMEX SG Love Dining + Lifestyle Credit merchant data.

LD truth: current AMEX Love Dining hotel + restaurant pages.
LC truth: current Platinum Credit Card Fashion & Dining Credit PDF.
'Both' is calculated at outlet/location level, not brand level.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

import pdfplumber
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LD_HOTELS_URL = "https://www.americanexpress.com/sg/benefits/love-dining/love-dining-hotels.html"
LD_RESTAURANTS_URL = "https://www.americanexpress.com/sg/benefits/love-dining/love-restaurants.html"
LC_PDF_URL = "https://www.americanexpress.com/content/dam/amex/en-sg/benefits/platinum-credit-card-fashion-dining-credit-participating-merchants.pdf"
UA = "Mozilla/5.0 (compatible; AmexSGBenefitFinder/1.0)"
POSTAL_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

ADDRESS_STOPS = (
    "find on map", "tel:", "telephone:", "visit website", "terms and conditions",
    "opening hours", "cuisine:", "advanced reservations", "please note", "blackout dates"
)
META_PREFIXES = (
    "cuisine:", "opening hours", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "daily", "weekday", "weekend", "lunch", "dinner", "breakfast", "last order", "closed",
    "*halal", "*muslim", "*vegetarian", "address", "tel:", "find on map", "visit website"
)


@dataclass
class Outlet:
    name: str
    brand: str
    address: str
    postal_code: str | None
    category: str
    ld: bool = False
    lc: bool = False
    ld_source: str | None = None
    lc_section: str | None = None
    match_note: str | None = None


def clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip(" \t\r\n,-")


def norm(value: str | None) -> str:
    s = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\b(the|restaurant|cafe|bar)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_address(value: str | None) -> str:
    s = unicodedata.normalize("NFKD", clean(value)).encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("road", "rd").replace("street", "st").replace("avenue", "ave")
    return re.sub(r"[^a-z0-9]+", "", s)


def postal(value: str | None) -> str | None:
    m = POSTAL_RE.search(value or "")
    return m.group(1) if m else None


def make_id(name: str, address: str, pc: str | None) -> str:
    raw = f"{norm(name)}|{pc or ''}|{norm_address(address)}".encode()
    return hashlib.sha1(raw).hexdigest()[:14]


def fetch(url: str) -> bytes:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"}, timeout=60)
    r.raise_for_status()
    return r.content


def visible_lines(html: bytes | str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return [clean(x) for x in soup.stripped_strings if clean(x)]


def is_meta_line(s: str) -> bool:
    low = clean(s).lower()
    if not low or any(low.startswith(p) for p in META_PREFIXES):
        return True
    return bool(re.fullmatch(r"[0-9.:\s\-–—apmAPM&]+", s))


def sublabel_before_address(block: list[str], addr_index: int, merchant: str) -> str | None:
    """Find multi-outlet label such as 'Bacha Coffee at ION Orchard'."""
    mnorm = norm(merchant)
    mtokens = set(mnorm.split())
    for j in range(addr_index - 1, max(-1, addr_index - 6), -1):
        cand = clean(block[j])
        if not cand or is_meta_line(cand):
            continue
        cn = norm(cand)
        overlap = len(mtokens & set(cn.split())) / max(1, len(mtokens))
        if cn == mnorm or cn.startswith(mnorm) or mnorm.startswith(cn) or overlap >= 0.5:
            return cand
    return None


def parse_ld_html(html: bytes | str, source: str) -> list[Outlet]:
    lines = visible_lines(html)
    marker = "love dining @ hotels partners" if source == "ld-hotel" else "love dining @ restaurants partners"
    start = next((i for i, x in enumerate(lines) if marker in x.lower()), -1)
    if start >= 0:
        lines = lines[start + 1:]

    detail_idx = [i for i, x in enumerate(lines) if x.lower() == "details"]
    outlets: list[Outlet] = []
    for n, di in enumerate(detail_idx):
        if di == 0:
            continue
        merchant = clean(lines[di - 1])
        if not merchant or len(merchant) > 120 or merchant.lower() in {"details", "terms and conditions"}:
            continue
        end = detail_idx[n + 1] - 1 if n + 1 < len(detail_idx) else min(len(lines), di + 180)
        block = lines[di + 1:end]
        seen_addr: set[str] = set()
        for i, line in enumerate(block):
            low = line.lower()
            if not low.startswith("address:"):
                continue
            first = clean(line.split(":", 1)[1]) if ":" in line else ""
            parts = [first] if first else []
            k = i + 1
            while k < len(block):
                nxt = clean(block[k])
                nlow = nxt.lower()
                if any(nlow.startswith(s) for s in ADDRESS_STOPS):
                    break
                parts.append(nxt)
                if postal(" ".join(parts)):
                    break
                k += 1
            address = clean(" ".join(parts))
            if not address or address in seen_addr:
                continue
            seen_addr.add(address)
            label = sublabel_before_address(block, i, merchant)
            outlets.append(Outlet(
                name=clean(label or merchant), brand=merchant, address=address,
                postal_code=postal(address), category="dining", ld=True, ld_source=source,
            ))
    return dedupe_outlets(outlets)


def table_pair(row: list[str | None]) -> tuple[str, str]:
    vals = [clean(x) for x in row if clean(x)]
    if not vals:
        return "", ""
    if len(row) >= 4:
        pivot = max(1, len(row) // 2)
        left = next((clean(x) for x in row[:pivot] if clean(x)), "")
        right = next((clean(x) for x in reversed(row[pivot:]) if clean(x)), "")
        if left and right:
            return left, right
    if len(vals) == 1:
        return "", vals[0]
    return vals[0], vals[-1]


def split_lc_location(cell: str, merchant: str) -> list[tuple[str, str]]:
    raw = (cell or "").strip()
    if not raw:
        return []
    paren = re.findall(r"([^()\n]+?)\s*\(([^()]*?\b\d{6}\b[^()]*)\)", raw, flags=re.I | re.S)
    if len(paren) >= 2:
        return [(clean(label), clean(addr)) for label, addr in paren]

    lines = [clean(x) for x in raw.splitlines() if clean(x)]
    if not postal(raw):
        return [(clean(merchant), clean(raw))]

    label = None
    addr_lines = lines[:]
    if len(lines) >= 2 and not re.search(r"\d", lines[0]) and postal(" ".join(lines[1:])):
        label = lines[0]
        addr_lines = lines[1:]
    return [(clean(label or merchant), clean(" ".join(addr_lines)))]


def lc_tables(pdf_bytes: bytes) -> list[tuple[str, list[list[str | None]]]]:
    """Associate extracted PDF tables with benefit sub-sections."""
    current = "fashion"
    output: list[tuple[str, list[list[str | None]]]] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").upper()
            tables = page.extract_tables() or []
            transitions: list[str] = []
            section_text = "" if page_index == 0 else text
            if "DINING BRANDS UNDER LOVE DINING PROGRAMME" in section_text:
                transitions.append("dining-love")
            if "MICHELIN-STARRED RESTAURANTS" in section_text:
                transitions.append("dining-michelin")
            if "ESTABLISHED DINING CONCEPTS" in section_text:
                transitions.append("dining-established")
            if "ESTABLISHED CHINESE DINING BRANDS" in section_text:
                transitions.append("dining-chinese")
            if transitions and len(tables) > 1:
                for t in tables[:-1]:
                    output.append((current, t))
                current = transitions[-1]
                output.append((current, tables[-1]))
            else:
                if transitions:
                    current = transitions[-1]
                for t in tables:
                    output.append((current, t))
    return output


def parse_lc_pdf(pdf_bytes: bytes) -> list[Outlet]:
    outlets: list[Outlet] = []
    previous_by_section: dict[str, str] = {}
    for section, table in lc_tables(pdf_bytes):
        category = "fashion" if section == "fashion" else "dining"
        for row in table:
            merchant_cell, location_cell = table_pair(row)
            if "MERCHANT" in merchant_cell.upper() or "ONLY ELIGIBLE" in location_cell.upper():
                continue
            if merchant_cell:
                previous_by_section[section] = merchant_cell
            merchant = previous_by_section.get(section, "")
            if not merchant or not location_cell:
                continue
            for display, address in split_lc_location(location_cell, merchant):
                outlets.append(Outlet(
                    name=display, brand=clean(merchant), address=address,
                    postal_code=postal(address), category=category, lc=True, lc_section=section,
                ))
    return dedupe_outlets(outlets)


def dedupe_outlets(items: Iterable[Outlet]) -> list[Outlet]:
    out: list[Outlet] = []
    seen = set()
    for x in items:
        key = (norm(x.name), x.postal_code or "", norm_address(x.address), x.ld, x.lc, x.ld_source, x.lc_section)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def name_score(a: Outlet, b: Outlet) -> float:
    aa = {norm(a.name), norm(a.brand)} - {""}
    bb = {norm(b.name), norm(b.brand)} - {""}
    best = 0.0
    for x in aa:
        for y in bb:
            if x == y:
                return 1.0
            if x in y or y in x:
                best = max(best, 0.92)
            best = max(best, SequenceMatcher(None, x, y).ratio())
    return best


def same_location(a: Outlet, b: Outlet) -> bool:
    if a.postal_code and b.postal_code:
        return a.postal_code == b.postal_code
    aa, bb = norm_address(a.address), norm_address(b.address)
    if aa and bb and (aa in bb or bb in aa):
        return True
    return SequenceMatcher(None, aa, bb).ratio() >= 0.82 if aa and bb else False


def merge_sources(ld: list[Outlet], lc: list[Outlet]) -> list[dict]:
    used_lc: set[int] = set()
    merged: list[Outlet] = []
    for d in ld:
        best_i, best_score = None, 0.0
        for i, c in enumerate(lc):
            if i in used_lc or c.category != "dining" or not same_location(d, c):
                continue
            score = name_score(d, c)
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None and best_score >= 0.72:
            c = lc[best_i]
            used_lc.add(best_i)
            merged.append(Outlet(
                name=d.name, brand=d.brand or c.brand, address=d.address or c.address,
                postal_code=d.postal_code or c.postal_code, category="dining",
                ld=True, lc=True, ld_source=d.ld_source, lc_section=c.lc_section,
                match_note=f"outlet+location match ({best_score:.2f})",
            ))
        else:
            merged.append(d)
    merged.extend(c for i, c in enumerate(lc) if i not in used_lc)

    by_key: dict[tuple, Outlet] = {}
    for x in merged:
        key = (norm(x.name), x.postal_code or "", norm_address(x.address))
        if key not in by_key:
            by_key[key] = x
        else:
            y = by_key[key]
            y.ld = y.ld or x.ld
            y.lc = y.lc or x.lc
            y.ld_source = y.ld_source or x.ld_source
            y.lc_section = y.lc_section or x.lc_section

    result = []
    for x in by_key.values():
        d = asdict(x)
        d["id"] = make_id(x.name, x.address, x.postal_code)
        d["lat"] = None
        d["lng"] = None
        result.append(d)
    return sorted(result, key=lambda x: (x["name"].lower(), x.get("postal_code") or "", x["address"].lower()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path, help="Local cached source files for testing/offline use")
    ap.add_argument("--lc-only", action="store_true", help="Offline bootstrap from LC PDF only")
    args = ap.parse_args()

    if args.source_dir:
        pdf = (args.source_dir / "lifestyle-credit.pdf").read_bytes()
        if args.lc_only:
            hotels = restaurants = b""
        else:
            hotels = (args.source_dir / "love-dining-hotels.html").read_bytes()
            restaurants = (args.source_dir / "love-dining-restaurants.html").read_bytes()
    else:
        pdf = fetch(LC_PDF_URL)
        hotels = b"" if args.lc_only else fetch(LD_HOTELS_URL)
        restaurants = b"" if args.lc_only else fetch(LD_RESTAURANTS_URL)

    ld = [] if args.lc_only else parse_ld_html(hotels, "ld-hotel") + parse_ld_html(restaurants, "ld-restaurant")
    lc = parse_lc_pdf(pdf)
    merchants = merge_sources(ld, lc)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "love_dining_hotels": LD_HOTELS_URL,
            "love_dining_restaurants": LD_RESTAURANTS_URL,
            "lifestyle_credit_pdf": LC_PDF_URL,
        },
        "stats": {
            "total": len(merchants), "ld": sum(x["ld"] for x in merchants),
            "lc": sum(x["lc"] for x in merchants),
            "both": sum(x["ld"] and x["lc"] for x in merchants),
            "fashion": sum(x["category"] == "fashion" for x in merchants),
            "dining": sum(x["category"] == "dining" for x in merchants),
        },
        "bootstrap_lc_only": bool(args.lc_only),
        "merchants": merchants,
    }
    DATA.mkdir(exist_ok=True)
    (DATA / "merchants.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["stats"], indent=2))
    if not args.lc_only and (payload["stats"]["ld"] < 20 or payload["stats"]["lc"] < 50):
        raise SystemExit("Sanity check failed: AMEX page/PDF structure may have changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
