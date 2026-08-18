from scripts.refresh_data_fixed import split_lc_location_fixed
from scripts.refresh_data import Outlet, merge_sources


def test_fairmont_grouped_cell_splits_all_four_outlets():
    cell = """ASIAN MARKET CAFÉ
80 BRAS BASAH ROAD, SINGAPORE 189560
ANTI:DOTE
80 BRAS BASAH ROAD, SINGAPORE 189560
PREGO
80 BRAS BASAH ROAD, SINGAPORE 189560
THE EIGHT
80 BRAS BASAH ROAD, SINGAPORE 189560"""
    rows = split_lc_location_fixed(cell, "FAIRMONT SINGAPORE")
    assert rows == [
        ("ASIAN MARKET CAFÉ", "80 BRAS BASAH ROAD, SINGAPORE 189560"),
        ("ANTI:DOTE", "80 BRAS BASAH ROAD, SINGAPORE 189560"),
        ("PREGO", "80 BRAS BASAH ROAD, SINGAPORE 189560"),
        ("THE EIGHT", "80 BRAS BASAH ROAD, SINGAPORE 189560"),
    ]


def test_prego_merges_as_both_at_same_outlet():
    ld = [Outlet("Prego", "Prego", "80 Bras Basah Road, Singapore 189560", "189560", "dining", ld=True)]
    lc = [Outlet("PREGO", "FAIRMONT SINGAPORE", "80 BRAS BASAH ROAD, SINGAPORE 189560", "189560", "dining", lc=True)]
    both = [x for x in merge_sources(ld, lc) if x["ld"] and x["lc"]]
    assert len(both) == 1
    assert both[0]["name"] == "Prego"
