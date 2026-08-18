from scripts.refresh_data import parse_ld_html, split_lc_location, Outlet, merge_sources


def test_ld_multi_outlet_and_inline_address():
    html="""<html><body><h2>Love Dining @ Restaurants Partners</h2>
    <div>Bacha Coffee</div><h3>Details</h3>
    <p>Bacha Coffee at Marina Bay Sands</p><div>Address:</div><div>2 Bayfront Ave, #B2-13/14, Singapore 018972</div><a>Find on map</a>
    <p>Bacha Coffee at ION Orchard</p><div>Address:</div><div>2 Orchard Turn #01-15/16 ION Orchard Mall, 238801</div><a>Find on map</a>
    <div>Les Bouchons</div><h3>Details</h3><p>Les Bouchons Rochester</p><div>Address:10 Rochester Park, Rochester Commons, Singapore 139221</div><a>Find on map</a>
    </body></html>"""
    rows=parse_ld_html(html,'ld-restaurant')
    assert len(rows)==3
    assert rows[0].name=='Bacha Coffee at Marina Bay Sands'
    assert rows[1].postal_code=='238801'
    assert rows[2].postal_code=='139221'


def test_lc_grouped_hotel_outlet():
    pairs=split_lc_location('ASIAN MARKET CAFÉ\n80 BRAS BASAH ROAD, SINGAPORE 189560','FAIRMONT SINGAPORE')
    assert pairs==[('ASIAN MARKET CAFÉ','80 BRAS BASAH ROAD, SINGAPORE 189560')]


def test_both_is_location_level_not_brand_level():
    ld=[Outlet('Bacha Coffee at ION Orchard','Bacha Coffee','2 Orchard Turn, Singapore 238801','238801','dining',ld=True)]
    lc=[Outlet('Bacha Coffee','Bacha Coffee','2 Bayfront Ave, Singapore 018972','018972','dining',lc=True)]
    assert not any(x['ld'] and x['lc'] for x in merge_sources(ld,lc))


def test_both_merges_same_outlet():
    ld=[Outlet('Asian Market Café','Asian Market Café','80 Bras Basah Road, Singapore 189560','189560','dining',ld=True)]
    lc=[Outlet('ASIAN MARKET CAFÉ','FAIRMONT SINGAPORE','80 BRAS BASAH ROAD, SINGAPORE 189560','189560','dining',lc=True)]
    both=[x for x in merge_sources(ld,lc) if x['ld'] and x['lc']]
    assert len(both)==1
