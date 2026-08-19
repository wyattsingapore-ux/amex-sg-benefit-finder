#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments" / "eatigo-live"
DATA = ROOT / "data" / "eatigo_today.json"
OUT = EXP / "preview.html"

html = (EXP / "index.html").read_text(encoding="utf-8")
app = (EXP / "app.js").read_text(encoding="utf-8")
data = json.loads(DATA.read_text(encoding="utf-8"))

payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
replacement = f"<script>window.EATIGO_TODAY={payload};</script>\n<script>{app}</script>"
html = html.replace('<script src="app.js"></script>', replacement)
if replacement not in html:
    raise SystemExit("Could not embed experiment app into preview")

OUT.write_text(html, encoding="utf-8")
print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
