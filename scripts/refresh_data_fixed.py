#!/usr/bin/env python3
"""Run the AMEX refresh with robust parsing for grouped LC merchant cells.

The AMEX Lifestyle Credit PDF sometimes stores several hotel restaurants in a
single 'ONLY ELIGIBLE FOR SPENDING(S) AT' cell. The original parser kept only
the first outlet. This wrapper patches split_lc_location before running the
normal refresh pipeline so each postal-code-terminated outlet is preserved.
"""
from __future__ import annotations

import re

from scripts import refresh_data as base


ADDRESS_WORDS = re.compile(
    r"\b(road|rd|street|st|avenue|ave|boulevard|blvd|drive|dr|lane|ln|walk|turn|quay|"
    r"promenade|place|plaza|gateway|central|square|park|crescent|way|terrace|mall|centre|center)\b",
    re.I,
)


def looks_like_address_start(line: str) -> bool:
    """Conservatively decide whether a first line is address text, not an outlet label."""
    s = base.clean(line)
    if not s:
        return False
    if base.POSTAL_RE.search(s) or "#" in s:
        return True
    if ADDRESS_WORDS.search(s) and re.search(r"\d", s):
        return True
    return False


def split_lc_location_fixed(cell: str, merchant: str) -> list[tuple[str, str]]:
    raw = (cell or "").strip()
    if not raw:
        return []

    # Preserve the existing parenthesised multi-location handling used by
    # merchants such as COMO Fashion.
    paren = re.findall(
        r"([^()\n]+?)\s*\(([^()]*?\b\d{6}\b[^()]*)\)",
        raw,
        flags=re.I | re.S,
    )
    if len(paren) >= 2:
        return [(base.clean(label), base.clean(addr)) for label, addr in paren]

    lines = [base.clean(x) for x in raw.splitlines() if base.clean(x)]
    postal_hits = base.POSTAL_RE.findall(raw)
    if not postal_hits:
        return [(base.clean(merchant), base.clean(raw))]

    # Ordinary one-location cells retain the old behaviour. The bug occurs
    # when a single PDF cell carries multiple restaurants/locations.
    if len(postal_hits) == 1:
        label = None
        addr_lines = lines[:]
        if (
            len(lines) >= 2
            and not looks_like_address_start(lines[0])
            and base.postal(" ".join(lines[1:]))
        ):
            label = lines[0]
            addr_lines = lines[1:]
        return [(base.clean(label or merchant), base.clean(" ".join(addr_lines)))]

    # Split a grouped cell at each completed Singapore postal code. This turns
    # e.g. Fairmont's Asian Market Cafe / Anti:dote / Prego / The Eight block
    # into four outlet chunks instead of one concatenated record.
    chunks: list[list[str]] = []
    buf: list[str] = []
    for line in lines:
        buf.append(line)
        if base.postal(" ".join(buf)):
            chunks.append(buf)
            buf = []
    if buf:
        if chunks:
            chunks[-1].extend(buf)
        else:
            chunks.append(buf)

    results: list[tuple[str, str]] = []
    for chunk in chunks:
        joined = base.clean(" ".join(chunk))
        if not base.postal(joined):
            continue
        label = base.clean(merchant)
        addr_lines = chunk
        if (
            len(chunk) >= 2
            and not looks_like_address_start(chunk[0])
            and base.postal(" ".join(chunk[1:]))
        ):
            label = base.clean(chunk[0])
            addr_lines = chunk[1:]
        results.append((label, base.clean(" ".join(addr_lines))))

    # Never silently collapse a multi-postal cell back to one record.
    if len(results) < len(postal_hits):
        raise RuntimeError(
            f"LC grouped-location parse lost outlets for {merchant!r}: "
            f"{len(postal_hits)} postal codes but {len(results)} parsed locations"
        )
    return results


# parse_lc_pdf resolves this function from refresh_data's module globals at
# runtime, so replacing the attribute patches the existing pipeline cleanly.
base.split_lc_location = split_lc_location_fixed


if __name__ == "__main__":
    raise SystemExit(base.main())
