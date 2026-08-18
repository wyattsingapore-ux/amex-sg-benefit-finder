#!/usr/bin/env python3
import json
from pathlib import Path

p=Path(__file__).resolve().parents[1]/'data'/'merchants.json'
d=json.loads(p.read_text(encoding='utf-8'))
ms=d.get('merchants',[])
assert ms, 'No merchants generated'
assert all(m.get('name') and m.get('address') for m in ms), 'Merchant missing name/address'

both=[m for m in ms if m.get('ld') and m.get('lc')]
assert all(m['category']=='dining' for m in both), 'Fashion merchant cannot be LD+LC'

gha=[m for m in ms if m.get('gha')]
gha_lc=[m for m in gha if m.get('lc')]
assert 10 <= len(gha) <= 40, f'Unexpected GHA Singapore outlet count: {len(gha)}'
assert all(m.get('category')=='dining' for m in gha), 'GHA list must be dining only'
assert all(m.get('gha_hotel') and m.get('gha_source') for m in gha), 'GHA merchant missing property/source metadata'
assert all(m.get('lc') and m.get('gha') for m in gha_lc), 'Invalid GHA+LC record'
assert d.get('sources',{}).get('gha_dining'), 'Missing official GHA dining source URL'

ids=[m['id'] for m in ms]
assert len(ids)==len(set(ids)), 'Duplicate merchant IDs'

print(json.dumps({
    'total':len(ms),
    'ld':sum(bool(m.get('ld')) for m in ms),
    'lc':sum(bool(m.get('lc')) for m in ms),
    'both':len(both),
    'gha':len(gha),
    'gha_lc':len(gha_lc),
},indent=2))
