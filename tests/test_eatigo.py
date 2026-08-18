from scripts.augment_eatigo import extract_branch_detail, slot_from_href, same_location, name_score


def test_eatigo_branch_address_and_postal():
    html='''<html><body><h1>Peppermint @ PARKROYAL COLLECTION Marina Bay</h1>
    <section><h2>About</h2><div>6 Raffles Boulevard, Singapore 039594, Singapore</div><div>Copy Address</div></section>
    </body></html>'''
    d=extract_branch_detail(html,'Peppermint','https://eatigo.com/en/branches/123')
    assert d['postal_code']=='039594'
    assert d['name'].startswith('Peppermint')


def test_eatigo_filters_non_singapore_branch():
    html='<html><body><h1>Food Exchange</h1><div>Johor Bahru, Malaysia</div><div>Copy Address</div></body></html>'
    assert extract_branch_detail(html,'Food Exchange','https://eatigo.com/en/branches/123') is None


def test_slot_date_time_discount_from_url():
    s=slot_from_href('18:30 -50 %','https://eatigo.com/en/branches/123?slot=2026-08-19+18%3A30')
    assert s=={'date':'2026-08-19','time':'18:30','discount':50}


def test_eatigo_lc_match_is_location_level():
    e={'name':'Peppermint @ PARKROYAL COLLECTION Marina Bay','address':'6 Raffles Boulevard, Singapore 039594','postal_code':'039594'}
    lc={'name':'PEPPERMINT','brand':'PARKROYAL COLLECTION MARINA BAY','address':'6 Raffles Boulevard, Singapore 039594','postal_code':'039594'}
    wrong={'name':'PEPPERMINT','brand':'Example','address':'10 Claymore Road, Singapore 229540','postal_code':'229540'}
    assert same_location(e,lc)
    assert name_score(e,lc)>=0.72
    assert not same_location(e,wrong)
