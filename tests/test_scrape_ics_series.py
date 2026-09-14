"""Per-date calendar entries for one weekly night collapse into a series."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

import scrape_ics

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_geocode(monkeypatch):
    import scraper_utils as su
    monkeypatch.setattr(su, "geocode", lambda loc: None)


def _vevent(uid, summary, start, location="288 Green St, Cambridge, MA", desc=""):
    end = start.replace("T210000", "T010000") if "T210000" in start else start
    return (
        "BEGIN:VEVENT\n"
        f"UID:{uid}@google.com\n"
        f"SUMMARY:{summary}\n"
        f"DTSTART;TZID=America/New_York:{start}\n"
        f"DTEND;TZID=America/New_York:{end}\n"
        f"LOCATION:{location}\n"
        + (f"DESCRIPTION:{desc}\n" if desc else "")
        + "END:VEVENT\n"
    )


def _calendar(*vevents):
    return "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n" + "".join(vevents) + "END:VCALENDAR\n"


FRIDAYS = ["20260918", "20260925", "20261002", "20261009"]


def test_per_date_copies_collapse_into_one_series():
    ics = _calendar(*[
        _vevent(f"f{i}", "Friday Night Bachata/Salsa @ Havana Club", f"{d}T210000")
        for i, d in enumerate(FRIDAYS)
    ])
    events = scrape_ics.parse_ics_feed(ics, source_id="sens", now=NOW, collapse_series=True)
    assert len(events) == 1
    ev = events[0]
    assert ev["recurring"] is True
    assert ev["startDate"].startswith("2026-09-18T21:00")
    assert [r[:10] for r in ev["recurrences"]] == ["2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09"]


def test_series_id_is_stable_across_scrapes():
    """Next week the first date is gone and a new one is appended; the id must
    not move, or the store sees a brand-new event every week."""
    week1 = _calendar(*[_vevent(f"a{i}", "Kiz Thursday", f"{d}T210000") for i, d in enumerate(FRIDAYS)])
    week2 = _calendar(*[_vevent(f"b{i}", "Kiz Thursday", f"{d}T210000") for i, d in enumerate(FRIDAYS[1:] + ["20261016"])])
    id1 = scrape_ics.parse_ics_feed(week1, source_id="sens", now=NOW, collapse_series=True)[0]["id"]
    id2 = scrape_ics.parse_ics_feed(week2, source_id="sens", now=NOW, collapse_series=True)[0]["id"]
    assert id1 == id2
    assert id1.startswith("sens-series-")


def test_different_nights_at_same_venue_stay_separate():
    ics = _calendar(
        _vevent("m1", "Sensual Bachata Mondays @ Havana Club", "20260921T200000"),
        _vevent("m2", "Sensual Bachata Mondays @ Havana Club", "20260928T200000"),
        _vevent("t1", "Salsa/Bachata Tuesdays @ Havana Club", "20260922T200000"),
        _vevent("t2", "Salsa/Bachata Tuesdays @ Havana Club", "20260929T200000"),
    )
    events = scrape_ics.parse_ics_feed(ics, source_id="sens", now=NOW, collapse_series=True)
    assert sorted(e["name"] for e in events) == [
        "Salsa/Bachata Tuesdays @ Havana Club",
        "Sensual Bachata Mondays @ Havana Club",
    ]
    assert all(len(e["recurrences"]) == 2 for e in events)


def test_singletons_and_rrule_events_pass_through():
    ics = _calendar(
        _vevent("s1", "Halloween Costume Party", "20261030T203000", location="35 Hampshire St, Cambridge, MA"),
        "BEGIN:VEVENT\nUID:r1@google.com\nSUMMARY:Rueda in the Pahk\n"
        "DTSTART;TZID=America/New_York:20260920T180000\nDTEND;TZID=America/New_York:20260920T200000\n"
        "RRULE:FREQ=WEEKLY;COUNT=3\nLOCATION:Jill Brown Rhone Park, Cambridge, MA\nEND:VEVENT\n",
    )
    events = scrape_ics.parse_ics_feed(ics, source_id="sens", now=NOW, collapse_series=True)
    by_name = {e["name"]: e for e in events}
    assert by_name["Halloween Costume Party"]["id"] == "s1@google.com"
    assert by_name["Halloween Costume Party"]["recurring"] is False
    assert by_name["Rueda in the Pahk"]["id"] == "r1@google.com"
    assert len(by_name["Rueda in the Pahk"]["recurrences"]) == 3


def test_series_borrows_details_from_a_richer_sibling():
    ics = _calendar(
        _vevent("x1", "Fuego y Candela Social", "20261010T213000", location="41 Hampshire St, Cambridge, MA"),
        _vevent("x2", "Fuego y Candela Social", "20261017T213000", location="41 Hampshire St, Cambridge, MA",
                desc="Price: $20 https://www.facebook.com/FuegoyCandelaSalsa/events"),
    )
    ev = scrape_ics.parse_ics_feed(ics, source_id="sens", now=NOW, collapse_series=True)[0]
    assert ev["url"] == "https://www.facebook.com/FuegoyCandelaSalsa/events"
    assert ev["cost"] == "$20"


def test_grouping_is_off_by_default():
    ics = _calendar(*[_vevent(f"f{i}", "Kiz Thursday", f"{d}T210000") for i, d in enumerate(FRIDAYS)])
    assert len(scrape_ics.parse_ics_feed(ics, source_id="sens", now=NOW)) == 4
