from scripts.augment_gha import parse_gha_singapore, name_score, same_location


def test_parse_gha_singapore_groups_outlets_under_properties():
    html='''<html><body>
    <div>SINGAPORE</div>
    <div>Pan Pacific Orchard</div><span>|</span>
    <a>Mosella</a>
    <div>Pan Pacific Singapore</div><span>|</span>
    <a>Atrium Lounge</a><a>Edge</a><a>Hai Tien Lo</a><a>Keyaki</a><a>Pacific Emporium</a><a>PLUME</a><a>Poolside Restaurant &amp; Bar</a>
    <div>PARKROYAL COLLECTION Marina Bay</div><span>|</span>
    <a>Peach Blossoms</a><a>Peppermint</a>
    <div>THAILAND</div>
    </body></html>'''
    rows=parse_gha_singapore(html)
    assert len(rows)==10
    assert rows[0]['name']=='Mosella'
    assert rows[0]['hotel']=='Pan Pacific Orchard'
    assert rows[0]['postal_code']=='229540'
    assert rows[-1]['name']=='Peppermint'
    assert rows[-1]['postal_code']=='039594'


def test_gha_name_normalization_handles_generic_restaurant_words():
    g={'name':'Lime Restaurant and Bar'}
    lc={'name':'LIME','brand':'PARKROYAL COLLECTION PICKERING'}
    assert name_score(g,lc)==1.0


def test_gha_lc_requires_same_location():
    g={'address':'80 Raffles Place, #60-01 UOB Plaza 1, Singapore 048624','postal_code':'048624'}
    same={'address':'80 RAFFLES PLACE UOB PLAZA 1, #60-01 SINGAPORE 048624','postal_code':'048624'}
    other={'address':'7500 Beach Road, Singapore 199591','postal_code':'199591'}
    assert same_location(g,same)
    assert not same_location(g,other)
