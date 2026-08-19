#!/usr/bin/env python3
"""Experiment-only full Eatigo rebuild.

Starts from the downloaded production merchants.json but removes prior Eatigo-only
rows and clears Eatigo fields from mixed-benefit rows before invoking the v2 full
Eatigo importer. This prevents stale Eatigo rows from being stacked on top of the
new full discovery during the isolated performance test.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MP = ROOT / "data" / "merchants.json"


def reset_existing_eatigo() -> tuple[int, int, int]:
    payload = json.loads(MP.read_text(encoding="utf-8"))
    original = list(payload.get("merchants", []))
    kept = []
    removed_eatigo_only = 0
    reset_mixed = 0

    for row in original:
        had_eatigo = bool(row.get("eatigo"))
        other_benefit = any(bool(row.get(k)) for k in ("ld", "lc", "gha", "accor"))

        if had_eatigo and not other_benefit:
            removed_eatigo_only += 1
            continue

        if had_eatigo:
            reset_mixed += 1

        row["eatigo"] = False
        row["eatigo_branch_id"] = None
        row["eatigo_url"] = None
        row["eatigo_location"] = None
        row["eatigo_match_note"] = None
        kept.append(row)

    payload["merchants"] = kept
    stats = payload.setdefault("stats", {})
    stats["eatigo"] = 0
    stats["eatigo_lc"] = 0
    MP.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "experiment_reset_original_rows": len(original),
        "experiment_removed_old_eatigo_only": removed_eatigo_only,
        "experiment_reset_mixed_eatigo_rows": reset_mixed,
        "experiment_rows_after_reset": len(kept),
    }, indent=2))
    return len(original), removed_eatigo_only, reset_mixed


def validate_unique_eatigo() -> None:
    payload = json.loads(MP.read_text(encoding="utf-8"))
    rows = [m for m in payload.get("merchants", []) if m.get("eatigo")]
    ids = [str(m.get("eatigo_branch_id") or "") for m in rows]
    missing = sum(not x for x in ids)
    unique = set(x for x in ids if x)
    duplicates = len(ids) - missing - len(unique)
    advertised = int(payload.get("eatigo_advertised_region_results") or 0)
    discovered = int(payload.get("eatigo_discovered_results") or 0)

    summary = {
        "eatigo_rows_after_rebuild": len(rows),
        "eatigo_unique_branch_ids": len(unique),
        "eatigo_duplicate_branch_rows": duplicates,
        "eatigo_missing_branch_ids": missing,
        "eatigo_discovered_region_branches": discovered,
        "eatigo_advertised_region_results": advertised,
    }
    print(json.dumps(summary, indent=2))

    if missing:
        raise SystemExit(f"Eatigo rebuild has {missing} rows without branch IDs")
    if duplicates:
        raise SystemExit(f"Eatigo rebuild has {duplicates} duplicate branch rows")
    if len(rows) < 200:
        raise SystemExit(f"Eatigo rebuild too small: {len(rows)} rows")
    if discovered and len(rows) > discovered:
        raise SystemExit(f"Singapore Eatigo rows exceed discovered region branches: {len(rows)} > {discovered}")


def main() -> int:
    reset_existing_eatigo()

    # Importing v2 patches the underlying resilient importer's pagination
    # functions. Call the patched implementation directly so this wrapper can
    # validate the resulting dataset afterward.
    from scripts import augment_eatigo_resilient_v2 as v2

    rc = int(v2.impl.main() or 0)
    if rc:
        return rc
    validate_unique_eatigo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
