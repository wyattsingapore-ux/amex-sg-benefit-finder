from scripts.augment_eatigo import (
    choose_lc_match,
    display_name_from_anchor,
    looks_non_singapore,
    parse_branch_detail,
    same_location,
    split_listing_name,
)


def test_eatigo_listing_name_cleanup():
    assert display_name_from_anchor('Peppermint @ PARKROYAL COLLECTION Marina Bay 4.7 7.6k reservations') == 'Peppermint @ PARKROYAL COLLECTION Marina Bay'
    assert split_listing_name('Peppermint @ PARKROYAL COLLECTION Marina Bay') == (
        'Peppermint', 'PARKROYAL COLLECTION Marina Bay'
    )


def test_eatigo_filters_obvious_johor_listing():
    assert looks_non_singapore('Fei Fan Hotpot @ Aeon Mall Tebrau City Johor')
    assert not looks_non_singapore('Lime Restaurant @ PARKROYAL COLLECTION Pickering, Singapore')


def test_eatigo_branch_address_only_extraction():
    html = '''<html><body><h1>2Gather @ Suntec City</h1>
    <section><h2>About</h2><div>3 Temasek Blvd, #01-505 Suntec Tower 2, Singapore 038983, Singapore</div><div>Copy Address</div></section>
    </body></html>'''
    d = parse_branch_detail(html, '2Gather @ Suntec City', 'https://eatigo.com/en/branches/123')
    assert d['postal_code'] == '038983'
    assert d['address'].startswith('3 Temasek Blvd')


def test_eatigo_lc_requires_same_location():
    e = {
        'name': 'Peppermint @ PARKROYAL COLLECTION Marina Bay',
        'address': '6 Raffles Boulevard, Singapore 039594, Singapore',
        'postal_code': '039594',
    }
    merchants = [
        {
            'name': 'PEPPERMINT', 'brand': 'PARKROYAL COLLECTION MARINA BAY',
            'address': '6 Raffles Boulevard, Singapore 039594', 'postal_code': '039594',
            'category': 'dining', 'lc': True,
        },
        {
            'name': 'PEPPERMINT', 'brand': 'Example Hotel',
            'address': '10 Claymore Road, Singapore 229540', 'postal_code': '229540',
            'category': 'dining', 'lc': True,
        },
    ]
    assert same_location(e, merchants[0])
    assert not same_location(e, merchants[1])
    i, note = choose_lc_match(e, merchants, set())
    assert i == 0
    assert 'location' in note


def test_eatigo_lc_does_not_match_same_name_wrong_location():
    e = {
        'name': 'Example Cafe',
        'address': '1 A Road, Singapore 111111',
        'postal_code': '111111',
    }
    merchants = [
        {'name': 'Example Cafe', 'brand': 'Hotel B', 'address': '2 B Road, Singapore 222222', 'postal_code': '222222', 'category': 'dining', 'lc': True},
    ]
    i, note = choose_lc_match(e, merchants, set())
    assert i is None
    assert note is None
