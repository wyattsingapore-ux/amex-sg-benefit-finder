#!/usr/bin/env python3
"""Geocode merchant locations with Singapore OneMap at build time."""
import json, os, time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
AUTH='https://www.onemap.gov.sg/api/auth/post/getToken'
SEARCH='https://www.onemap.gov.sg/api/common/elastic/search'


def get_token():
    if os.getenv('ONEMAP_TOKEN'):
        return os.environ['ONEMAP_TOKEN']
    email=os.getenv('ONEMAP_API_EMAIL') or os.getenv('ONEMAP_EMAIL')
    password=os.getenv('ONEMAP_API_PASSWORD') or os.getenv('ONEMAP_PASSWORD')
    if not email or not password:
        return None
    r=requests.post(AUTH,json={'email':email,'password':password},timeout=30)
    r.raise_for_status()
    data=r.json()
    if not data.get('access_token'):
        raise RuntimeError(f'OneMap authentication failed: {data}')
    return data['access_token']


def search(query,tok):
    r=requests.get(SEARCH,params={'searchVal':query,'returnGeom':'Y','getAddrDetails':'Y','pageNum':1},headers={'Authorization':tok},timeout=30)
    r.raise_for_status()
    data=r.json()
    if data.get('error'):
        raise RuntimeError(data['error'])
    return data.get('results') or []


def choose(results,pc):
    if pc:
        exact=[r for r in results if str(r.get('POSTAL') or '')==pc]
        if exact: return exact[0]
    return results[0] if results else None


def main():
    mp=DATA/'merchants.json'; cp=DATA/'geocodes.json'
    payload=json.loads(mp.read_text(encoding='utf-8'))
    cache=json.loads(cp.read_text(encoding='utf-8')) if cp.exists() else {}
    tok=get_token(); new=failed=0
    if not tok:
        print('No OneMap credentials/token found; skipping new geocoding and preserving cache.')
    for m in payload['merchants']:
        query=(m.get('postal_code') or m.get('address') or m.get('name') or '').strip()
        key=query.lower(); hit=cache.get(key)
        if not hit and tok and query:
            try:
                choice=choose(search(query,tok),m.get('postal_code'))
                if choice:
                    hit={'lat':float(choice['LATITUDE']),'lng':float(choice.get('LONGITUDE') or choice.get('LONGTITUDE')),
                         'matched_address':choice.get('ADDRESS'),'postal':choice.get('POSTAL'),'query':query}
                    cache[key]=hit; new+=1
                else: failed+=1
            except Exception as e:
                print(f'WARN {query!r}: {e}'); failed+=1
            time.sleep(.12)
        if hit:
            m['lat']=hit.get('lat'); m['lng']=hit.get('lng')
    cp.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
    mp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    mapped=sum(m.get('lat') is not None and m.get('lng') is not None for m in payload['merchants'])
    print(json.dumps({'mapped':mapped,'total':len(payload['merchants']),'new_cache_entries':new,'failures':failed},indent=2))

if __name__=='__main__': main()
