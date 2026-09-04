"""A live weekly series must not be archived by a stale recurrences[] list.

"Rueda in the Pahk" ran every Sunday through the fall while its stored
recurrences[] still ended in August. The scrape carried the current dates, but
merge_event() refreshed only startDate/endDate/dayOfWeek on a same-id re-scrape,
so archive_past_events() — which dated recurring events by recurrences[-1] —
filed a running series away, silently, with no queue entry to notice it.

Same tmp-dir isolation pattern as test_quarantine.py.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es




NY = ZoneInfo("America/New_York")


def _sunday(weeks_from_now: int) -> str:
    """6 PM Boston, `weeks_from_now` Sundays away. Negative goes backwards.

    Relative like the other fixtures: hardcoded dates rot into the past and
    take unrelated assertions down with them.
    """
    now = datetime.now(NY)
    days = (6 - now.weekday()) % 7 + 7 * weeks_from_now
    return (now + timedelta(days=days)).replace(
        hour=18, minute=0, second=0, microsecond=0
    ).isoformat()


def _series(recurrences: list[str], start: str | None = None) -> dict:
    return {
        "id": "rueda-in-the-pahk",
        "name": "Rueda in the Pahk",
        "startDate": start or recurrences[0],
        "endDate": start or recurrences[0],
        "location": "Jill Brown Rhone Park, 900 Main St, Cambridge, MA 02139",
        "lat": 42.3653,
        "lng": -71.1034,
        "description": "Free rueda de casino lesson, then social dancing.",
        "url": "https://example.org/rueda",
        "styles": ["salsa"],
        "cost": "Free",
        "recurring": True,
        "source": "beatrice-calendar",
        "recurrences": recurrences,
    }


def test_stale_recurrences_do_not_archive_a_running_series(store):
    """The exact shape that broke: future startDate, months-old recurrences."""
    stale = _series(
        [_sunday(-4), _sunday(-3), _sunday(-2), _sunday(-1)],
        start=_sunday(1),
    )
    store.save_active([stale])

    archived = store.archive_past_events()

    assert archived == []
    assert [e["id"] for e in store.load_active()] == ["rueda-in-the-pahk"]


def test_rescrape_refreshes_the_recurrence_list(store):
    """A same-id re-scrape carries its dates, not just its startDate."""
    store.save_active([_series([_sunday(-2), _sunday(-1)])])

    fresh = [_sunday(1), _sunday(2), _sunday(3)]
    result = store.add_event(_series(fresh), skip_latin_check=True)

    assert result["status"] == "duplicate"
    assert result["existing"]["recurrences"] == fresh


def test_single_occurrence_rescrape_keeps_the_stored_series(store):
    """An incoming copy with no recurrences[] is one night, not a cancellation."""
    known = [_sunday(1), _sunday(2), _sunday(3)]
    store.save_active([_series(known)])

    one_night = _series(known)
    one_night.pop("recurrences")
    one_night["startDate"] = one_night["endDate"] = _sunday(1)
    store.add_event(one_night, skip_latin_check=True)

    assert store.load_active()[0]["recurrences"] == known


def test_rescrape_with_replaced_uid_extends_same_source_series(store):
    """A calendar UID change must not strand the stored recurrence window."""
    old_dates = [_sunday(-1), _sunday(1), _sunday(2)]
    store.save_active([_series(old_dates, start=old_dates[0])])

    fresh_dates = [_sunday(1), _sunday(2), _sunday(3), _sunday(4)]
    fresh = _series(fresh_dates, start=fresh_dates[0])
    fresh["id"] = "replacement-calendar-uid"
    result = store.add_event(fresh, skip_latin_check=True)

    assert result["status"] == "duplicate"
    stored = store.load_active()[0]
    assert stored["id"] == "rueda-in-the-pahk"
    assert stored["startDate"] == fresh_dates[0]
    assert stored["recurrences"] == fresh_dates


def test_archived_series_refresh_merges_into_existing_active_occurrence(store):
    """An archived old UID must not reactivate beside its active duplicate."""
    event_date = _sunday(1)
    active = _series([event_date], start=event_date)
    active["id"] = "current-one-off"
    active["recurring"] = False
    active.pop("recurrences")
    store.save_active([active])

    archived = _series([_sunday(-3), _sunday(-2)])
    store.save_archive([archived])

    future = [event_date, _sunday(2), _sunday(3)]
    incoming = _series(future, start=event_date)
    result = store.add_event(incoming, skip_latin_check=True)

    assert result["status"] == "duplicate"
    assert result["retired_archive"] == "rueda-in-the-pahk"
    assert store.load_archive() == []
    stored = store.load_active()
    assert len(stored) == 1
    assert stored[0]["id"] == "current-one-off"
    assert stored[0]["recurrences"] == future


def test_genuinely_finished_series_still_archives(store):
    """The guard must not keep a series alive after its last night."""
    store.save_active([_series([_sunday(-3), _sunday(-2), _sunday(-1)])])

    archived = store.archive_past_events()

    assert [e["id"] for e in archived] == ["rueda-in-the-pahk"]
    assert store.load_active() == []
