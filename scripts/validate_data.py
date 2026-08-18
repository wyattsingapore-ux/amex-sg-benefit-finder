#!/usr/bin/env python3
import json
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'data'/'merchants.json'
d=json.loads(p.read_text(encoding='utf-8')); ms=d.get('merchants',[])
assert ms, 'No merchants generated'
assert all(m.get('name') and m.get('address') for m in ms), 'Merchant missing name/address'
both=[m for m in ms if m.get('ld') and m.get('lc')]
assert all(m['category']=='dining' for m in both), 'Fashion merchant cannot be LD+LC'
ids=[m['id'] for m in ms]; assert len(ids)==len(set(ids)), 'Duplicate merchant IDs'
print(json.dumps({'total':len(ms),'ld':sum(m['ld'] for m in ms),'lc':sum(m['lc'] for m in ms),'both':len(both)},indent=2))
