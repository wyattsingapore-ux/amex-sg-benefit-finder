from scripts.augment_eatigo import (
    choose_lc_match,
    display_name_from_anchor,
    looks_non_singapore,
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


def test_eatigo_lc_prefers_matching_branch_qualifier():
    e = {'name': 'Peppermint @ PARKROYAL COLLECTION Marina Bay'}
    merchants = [
        {
            'name': 'PEPPERMINT', 'brand': 'PARKROYAL COLLECTION MARINA BAY',
            'address': '6 Raffles Boulevard, Singapore 039594', 'category': 'dining', 'lc': True,
        },
        {
            'name': 'PEPPERMINT', 'brand': 'Example Hotel',
            'address': '10 Claymore Road, Singapore 229540', 'category': 'dining', 'lc': True,
        },
    ]
    i, note = choose_lc_match(e, merchants, set())
    assert i == 0
    assert 'branch' in note


def test_eatigo_lc_does_not_guess_between_ambiguous_branches():
    e = {'name': 'Example Cafe'}
    merchants = [
        {'name': 'Example Cafe', 'brand': 'Hotel A', 'address': '1 A Road', 'category': 'dining', 'lc': True},
        {'name': 'Example Cafe', 'brand': 'Hotel B', 'address': '2 B Road', 'category': 'dining', 'lc': True},
    ]
    i, note = choose_lc_match(e, merchants, set())
    assert i is None
    assert note is None
