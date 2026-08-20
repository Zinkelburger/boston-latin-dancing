"""Tests for the J&L Dance Studio announcement-bar scraper."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_jandl as jandl

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 20, 15, 0, tzinfo=NY)


@pytest.fixture(autouse=True)
def no_geocode(monkeypatch):
    # Offsite fests pass location without coords; don't hit Nominatim in unit tests.
    import scraper_utils as su
    monkeypatch.setattr(su, "geocode", lambda loc: None)


ANNOUNCEMENT_HTML = """
<p><u>UPCOMING EVENTS</u></p>
<ul>
<li><p><strong>August 17th:</strong> Intro to Salsa Shines Workshop; Bachata Footwork Workshop; Salsa Practice Social</p></li>
<li><p><strong>August 19:</strong> Merengue Open Level Workshop</p></li>
<li><p><strong>August 21-23:</strong> Boston Salsa Fest - code jnl26</p></li>
<li><p><strong>August 24: </strong>Studio Closed</p></li>
<li><p><strong>August 26: </strong>Social Dance Safety Workshop Open Level</p></li>
<li><p><strong>August 31st-September 8th:</strong> Studio Closed</p></li>
<li><p><strong>September 9th &amp; 14th</strong>:  New Beginner Cycles Begin</p></li>
<li><p><strong>October 3rd:</strong> J&amp;L Underground Social</p></li>
</ul>
"""


def test_parses_all_dated_bar_items():
    items = jandl.parse_announcement_items(ANNOUNCEMENT_HTML, NOW)
    titles = [i["title"] for i in items]
    assert len(items) == 9  # Sep 9 & 14 is two dated rows
    assert "Salsa Practice Social" in titles[0] or "Intro to Salsa Shines" in titles[0]
    assert items[0]["start"].strftime("%Y-%m-%d") == "2026-08-17"
    # range — "August 21-23" is stored with an exclusive end, the way the
    # calendar feeds write an all-day DTEND, so the UI renders one date range
    # from either source instead of inventing midnight-to-midnight hours.
    fest = next(i for i in items if "Salsa Fest" in i["title"])
    assert fest["start"].strftime("%Y-%m-%d") == "2026-08-21"
    assert fest["end"].strftime("%Y-%m-%d") == "2026-08-24"
    # two dates
    cycles = [i for i in items if "Beginner Cycles" in i["title"]]
    assert [i["start"].strftime("%Y-%m-%d") for i in cycles] == ["2026-09-09", "2026-09-14"]
    underground = next(i for i in items if "Underground" in i["title"])
    assert underground["start"].strftime("%Y-%m-%d") == "2026-10-03"


def test_keeps_socials_and_fests_drops_workshops_and_closures():
    assert jandl.is_danceable("Salsa Practice Social")
    assert jandl.is_danceable("J&L Underground Social")
    assert jandl.is_danceable("Boston Salsa Fest")
    assert jandl.is_danceable("Halloween Party")
    assert not jandl.is_danceable("Merengue Open Level Workshop")
    assert not jandl.is_danceable("Social Dance Safety Workshop Open Level")
    assert not jandl.is_danceable("Studio Closed")
    assert not jandl.is_danceable("New Beginner Cycles Begin")
    assert not jandl.is_danceable("Intro to Salsa Shines Workshop")


def test_mixed_line_emits_only_the_social():
    items = jandl.parse_announcement_items(ANNOUNCEMENT_HTML, NOW)
    events = jandl.items_to_events(items, jandl.EVENTS_URL)
    names_on_17 = [
        e["name"] for e in events if e["startDate"].startswith("2026-08-17")
    ]
    assert names_on_17 == ["Salsa Practice Social"]
    assert all("Workshop" not in e["name"] for e in events)


def test_underground_gets_standing_hours_and_studio_pin():
    items = jandl.parse_announcement_items(ANNOUNCEMENT_HTML, NOW)
    events = jandl.items_to_events(items, jandl.EVENTS_URL)
    ug = next(e for e in events if "Underground" in e["name"])
    assert ug["startDate"].startswith("2026-10-03T19:00")
    assert ug["endDate"].startswith("2026-10-03T23:00")
    assert ug["cost"] == "$15"
    assert ug["url"] == jandl.UNDERGROUND_URL
    assert ug["lat"] == jandl.STUDIO_LAT
    assert "bachata" in ug["styles"]


def test_promoted_fest_is_offsite_and_keeps_promo_code():
    items = jandl.parse_announcement_items(ANNOUNCEMENT_HTML, NOW)
    events = jandl.items_to_events(items, jandl.EVENTS_URL)
    fest = next(e for e in events if "Salsa Fest" in e["name"])
    assert fest["name"] == "Boston Salsa Fest"
    # The bar never names the venue. Geocoding "Boston, MA" would pin the fest
    # on City Hall, so the event ships without coordinates instead.
    assert fest["location"] == "Boston, MA"
    assert fest["venueUnknown"] is True
    assert fest["lat"] is None
    assert fest["lng"] is None
    assert "jnl26" in fest["description"]
    assert fest["startDate"].startswith("2026-08-21")
    assert fest["endDate"].startswith("2026-08-24")  # exclusive; last day is the 23rd


def test_empty_bar_is_unparseable():
    assert jandl.parse_announcement_items("", NOW) == []
    assert jandl.parse_announcement_items("<p>hello</p>", NOW) == []


def test_recent_past_keeps_this_year_stale_mid_year_does_not_roll():
    # Aug 17 is only 3 days ago on Aug 20 — keep 2026, let future-filter drop it.
    items = jandl.parse_date_head("August 17th: Salsa Practice Social", NOW)
    assert items[0]["start"].year == 2026
    # A March leftover in August would land ~7 months out if rolled — treat as stale.
    stale = jandl.parse_date_head("March 1st: Salsa Practice Social", NOW)
    assert stale == []


def test_venue_unknown_listing_is_never_geocoded(monkeypatch):
    """The offline test env geocodes to None anyway, so pin the real contract:
    a region-only location must not be resolved to that region's centroid."""
    import scraper_utils

    monkeypatch.setattr(scraper_utils, "geocode", lambda _loc: (42.3588336, -71.0578303))
    ev = scraper_utils.make_event(
        id="x", name="Boston Salsa Fest", start=NOW, location="Boston, MA",
        venue_unknown=True,
    )
    assert ev["lat"] is None and ev["lng"] is None
    assert ev["venueUnknown"] is True

    pinned = scraper_utils.make_event(
        id="y", name="Some Social", start=NOW, location="Boston, MA",
    )
    assert pinned["lat"] == 42.3588336
    assert "venueUnknown" not in pinned
