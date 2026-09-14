"""Tests for the Buena Vibra (formerly J&L Dance Studio) socials-page scraper."""

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_jandl as jandl

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 14, 15, 0, tzinfo=NY)


@pytest.fixture(autouse=True)
def no_geocode(monkeypatch):
    import scraper_utils as su
    monkeypatch.setattr(su, "geocode", lambda loc: None)


# Trimmed from the live page on 2026-09-14: nav, one content block, footer.
SOCIALS_HTML = """
<html><head><title>SOCIALS | Join the Dance Fun Today — Buena Vibra Dance Studio</title>
<script>var x = "Upcoming Social Jan. 1st";</script></head>
<body><nav><a href="/">HOME</a><a href="/classes">CLASSES</a><a href="/socials">SOCIALS</a></nav>
<section><h1>Dance Socials</h1>
<h2>Upcoming Social Oct. 3rd</h2>
<p><strong>COST:</strong> $15 CASH ONLY</p>
<p><strong>TIME:</strong> 7-11PM</p>
<p><strong>MUSIC FORMAT:</strong> 70% BACHATA, 30% SALSA</p>
<p>PARKING: AMPLE STREET PARKING FREE AFTER 7PM, CBD &amp; JACKSON STREET GARAGES $1.25/HOUR</p>
<p>TRAIN: 5 MINUTE WALK FROM MALDEN CENTER T STOP ORANGE LINE</p>
<p>**** ALL LEVELS WELCOME ****</p></section>
<footer>Buena Vibra Dance Studio 75 Pleasant Street, Suite 125, 1st Floor Malden, MA 02148</footer>
</body></html>
"""


def test_parses_the_upcoming_social_row():
    rows = jandl.parse_socials_page(SOCIALS_HTML, NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row["date"].strftime("%Y-%m-%d") == "2026-10-03"
    assert row["hours"] == ((19, 0), (23, 0))
    assert row["cost"] == "$15 CASH ONLY"
    assert row["music_format"] == "70% BACHATA, 30% SALSA"


def test_script_text_is_not_mistaken_for_content():
    # The <script> block also says "Upcoming Social"; only the visible heading counts.
    rows = jandl.parse_socials_page(SOCIALS_HTML, NOW)
    assert [r["date"].month for r in rows] == [10]


def test_row_becomes_a_pinned_studio_event_with_page_hours():
    rows = jandl.parse_socials_page(SOCIALS_HTML, NOW)
    ev = jandl.row_to_event(rows[0], jandl.SOCIALS_URL)
    assert ev["id"] == "jandl-20261003-buena-vibra-social"
    assert ev["name"] == jandl.SOCIAL_NAME
    assert ev["startDate"].startswith("2026-10-03T19:00")
    assert ev["endDate"].startswith("2026-10-03T23:00")
    assert ev["cost"] == "$15 CASH ONLY"
    assert ev["styles"] == ["bachata", "salsa"]
    assert ev["lat"] == jandl.STUDIO_LAT and ev["lng"] == jandl.STUDIO_LNG
    assert ev["url"] == jandl.SOCIALS_URL
    assert "formerly J&L" in ev["description"]
    assert ev["organizer"] == jandl.ORGANIZER


def test_multiple_dates_on_the_heading_each_get_an_event():
    html = SOCIALS_HTML.replace("Upcoming Social Oct. 3rd", "Upcoming Socials: Oct. 3rd &amp; Nov 7th")
    rows = jandl.parse_socials_page(html, NOW)
    assert [r["date"].strftime("%m-%d") for r in rows] == ["10-03", "11-07"]


def test_hours_that_cross_midnight_end_next_day():
    html = SOCIALS_HTML.replace("7-11PM", "9PM-1AM")
    ev = jandl.row_to_event(jandl.parse_socials_page(html, NOW)[0], jandl.SOCIALS_URL)
    assert ev["startDate"].startswith("2026-10-03T21:00")
    assert ev["endDate"].startswith("2026-10-04T01:00")


def test_missing_time_falls_back_to_standing_hours():
    html = SOCIALS_HTML.replace("<p><strong>TIME:</strong> 7-11PM</p>", "")
    row = jandl.parse_socials_page(html, NOW)[0]
    assert row["hours"] is None
    ev = jandl.row_to_event(row, jandl.SOCIALS_URL)
    assert ev["startDate"].startswith("2026-10-03T19:00")
    assert ev["endDate"].startswith("2026-10-03T23:00")


def test_page_without_heading_is_unparseable():
    assert jandl.parse_socials_page("", NOW) == []
    assert jandl.parse_socials_page("<h1>Dance Socials</h1><p>Check back soon</p>", NOW) == []


def test_stale_date_does_not_roll_a_year():
    # A May leftover in September would land ~8 months out if rolled — treat as stale.
    html = SOCIALS_HTML.replace("Oct. 3rd", "May 1st")
    assert jandl.parse_socials_page(html, NOW) == []


def test_venue_unknown_listing_is_never_geocoded(monkeypatch):
    """A region-only location must not be resolved to that region's centroid."""
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
