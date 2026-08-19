#!/usr/bin/env python3
"""EXPERIMENT ONLY: fetch today's Eatigo time/discount slots without changing production data.

This script belongs to the experiment/eatigo-live-discounts branch. It reads an
existing merchants.json, visits Eatigo branch pages over ordinary HTTP, extracts
the booking-widget time/discount pairs for the Singapore-local current date,
and writes a separate eatigo_today.json snapshot.

It never modifies merchants.json and does not use the production Eatigo crawler.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

SG = ZoneInfo("Asia/Singapore")
UA = "Mozilla/5.0 (compatible; DiningBenefitFinder-EatigoExperiment/0.1)"
TIME_DISCOUNT_RE = re.compile(r"(?<!\d)([0-2]?\d:[0-5]\d)\s*-?\s*(\d{1,3})\s*%")
BRANCH_ID_RE = re.compile(r"/branches/(\d+)")


def with_today(url: str, now: datetime) -> str:
    """Force a branch page to render the Singapore-local date being tested."""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    # The exact time only selects the date context; all visible slots are parsed.
    q["slot"] = f"{now.date().isoformat()} 12:00"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def branch_id(url: str) -> str | None:
    m = BRANCH_ID_RE.search(url or "")
    return m.group(1) if m else None


def parse_slots(html: str, now: datetime) -> list[dict]:
    """Parse future time/discount pairs from the branch booking widget.

    Eatigo currently server-renders the slot strings. We start at the last
    'Business Hours' marker to avoid menu/review discount percentages higher up
    the page, then de-duplicate repeated booking-summary values.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = "\n".join(soup.stripped_strings)
    marker = text.rfind("Business Hours")
    tail = text[marker:] if marker >= 0 else text

    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    current_minutes = now.hour * 60 + now.minute
    for t, pct in TIME_DISCOUNT_RE.findall(tail):
        hh, mm = (int(x) for x in t.split(":"))
        if hh > 23:
            continue
        discount = int(pct)
        if not 0 < discount <= 100:
            continue
        minutes = hh * 60 + mm
        if minutes < current_minutes:
            continue
        key = (f"{hh:02d}:{mm:02d}", discount)
        if key in seen:
            continue
        seen.add(key)
        out.append({"time": key[0], "discount": discount})

    out.sort(key=lambda x: x["time"])
    return out


def fetch_one(row: dict, now: datetime, session: requests.Session) -> dict:
    url = row.get("eatigo_url") or ""
    bid = row.get("eatigo_branch_id") or branch_id(url)
    result = {
        "id": row.get("id"),
        "name": row.get("name"),
        "branch_id": bid,
        "eatigo_url": url,
        "address": row.get("address"),
        "postal_code": row.get("postal_code"),
        "lat": row.get("lat"),
        "lng": row.get("lng"),
        "lc": bool(row.get("lc")),
        "slots": [],
        "best_today": None,
        "error": None,
    }
    if not url:
        result["error"] = "missing Eatigo URL"
        return result

    req_url = with_today(url, now)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            r = session.get(req_url, headers={"User-Agent": UA, "Accept-Language": "en-SG,en;q=0.9"}, timeout=35)
            r.raise_for_status()
            slots = parse_slots(r.text, now)
            result["slots"] = slots
            result["best_today"] = max((s["discount"] for s in slots), default=None)
            return result
        except Exception as exc:  # keep experiment resilient and observable
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    result["error"] = str(last_error)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchants", default="data/merchants.json")
    ap.add_argument("--output", default="data/eatigo_today.json")
    ap.add_argument("--limit", type=int, default=30, help="0 means all Eatigo rows")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    now = datetime.now(SG)
    merchants_path = Path(args.merchants)
    payload = json.loads(merchants_path.read_text(encoding="utf-8"))
    rows = [m for m in payload.get("merchants", []) if m.get("eatigo") and m.get("eatigo_url")]
    if args.limit > 0:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("No Eatigo merchant rows available for experiment")

    session = requests.Session()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch_one, row, now, session) for row in rows]
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda x: (str(x.get("name") or "").lower(), str(x.get("branch_id") or "")))
    usable = [x for x in results if x.get("slots")]
    out = {
        "experiment": "eatigo-live-discounts-v1",
        "date_sg": now.date().isoformat(),
        "fetched_at": now.isoformat(),
        "source": "Eatigo branch pages (ordinary HTTP; no Playwright)",
        "restaurants_attempted": len(results),
        "restaurants_with_future_slots": len(usable),
        "restaurants_with_50pct_or_better": sum((x.get("best_today") or 0) >= 50 for x in usable),
        "restaurants": results,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ["date_sg", "restaurants_attempted", "restaurants_with_future_slots", "restaurants_with_50pct_or_better"]}, indent=2))

    # For an experiment, require enough successful samples to prove the method
    # but do not demand perfection or touch production deployment.
    if len(usable) < max(3, int(len(results) * 0.30)):
        raise SystemExit(f"Experiment produced too few usable slot pages: {len(usable)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
