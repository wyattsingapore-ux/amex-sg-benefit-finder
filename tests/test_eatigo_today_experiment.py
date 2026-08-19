from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.experiment_eatigo_today import parse_cuisines, parse_slots, with_today

SG = ZoneInfo("Asia/Singapore")


def test_parse_future_slots_only():
    html = '''<html><body><div>Menu -30 %</div><div>Business Hours</div>
    <div>12:00 -20 %</div><div>14:00 -50 %</div><div>18:30 -40 %</div>
    <div>2 People 18:30 / -40%</div></body></html>'''
    now = datetime(2026, 8, 19, 13, 37, tzinfo=SG)
    assert parse_slots(html, now) == [
        {"time": "14:00", "discount": 50},
        {"time": "18:30", "discount": 40},
    ]


def test_parse_source_cuisines():
    html = '''<html><body><h2>About</h2><div>Cuisines</div>
    <div>International, American Cuisine</div><div>Atmospheres</div>
    <div>Casual Dining, Bistro</div><div>Business Hours</div></body></html>'''
    assert parse_cuisines(html) == ["International", "American"]


def test_parse_cuisines_from_separate_nodes():
    html = '''<html><body><span>Cuisines</span><a>Japanese Cuisine</a>
    <a>Asian Fusion</a><span>Spoken Languages</span><span>English</span></body></html>'''
    assert parse_cuisines(html) == ["Japanese", "Asian Fusion"]


def test_force_singapore_date_context():
    now = datetime(2026, 8, 19, 13, 37, tzinfo=SG)
    url = with_today("https://eatigo.com/en/branches/12345?foo=bar", now)
    assert "foo=bar" in url
    assert "slot=2026-08-19+12%3A00" in url
