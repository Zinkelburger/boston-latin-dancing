"""Nth-weekday hubs only suppress scraped events on dates they actually generate.

Fixture uses a Dante-shaped 1st–4th Friday schedule. A scraped 5th-Friday bonus
social must surface as its own pin instead of being swallowed by a hub that
won't show that date. (The live `dantes-tambo` venue is now
`nextDateApproximate` — this tests the coverage helper, not the live config.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from event_store import _venue_schedule_covers_event


def _dante_hub(schedule):
    return {
        "id": "dantes-tambo",
        "name": "Dante's Salsa Fridays",
        "location": "Dante Alighieri Society, 41 Hampshire St, Cambridge, MA 02139",
        "schedule": schedule,
        "recurring": True,
        "source": "recurring-venues",
    }


NTH_SCHEDULE = [
    {"dayOfWeek": "Friday", "time": "8:30 PM – 1:00 AM", "note": "1st Friday of the month — Dante's Salsa Inferno"},
    {"dayOfWeek": "Friday", "time": "8:45 PM – 1:00 AM", "note": "2nd Friday of the month — Tambó Salsa Social"},
    {"dayOfWeek": "Friday", "time": "8:30 PM – 1:00 AM", "note": "3rd Friday of the month — Dante's Salsa Inferno"},
    {"dayOfWeek": "Friday", "time": "8:45 PM – 1:00 AM", "note": "4th Friday of the month — Tambó Salsa Social"},
]


def _scraped(start):
    return {"id": "x", "name": "Tambo Salsa Social", "startDate": start}


def test_nth_friday_dates_are_covered():
    hub = _dante_hub(NTH_SCHEDULE)
    # Aug 7 (1st), Aug 14 (2nd), Aug 21 (3rd), Aug 28 (4th Friday of Aug 2026)
    for start in [
        "2026-08-07T20:30:00-04:00",
        "2026-08-14T20:45:00-04:00",
        "2026-08-21T21:00:00-04:00",
        "2026-08-28T20:45:00-04:00",
    ]:
        assert _venue_schedule_covers_event(hub, _scraped(start), "Friday")


def test_fifth_friday_is_not_covered():
    hub = _dante_hub(NTH_SCHEDULE)
    # July 31, 2026 is a 5th Friday — the hub never generates it.
    assert not _venue_schedule_covers_event(hub, _scraped("2026-07-31T20:30:00-04:00"), "Friday")


def test_wrong_weekday_is_not_covered():
    hub = _dante_hub(NTH_SCHEDULE)
    assert not _venue_schedule_covers_event(hub, _scraped("2026-08-01T21:00:00-04:00"), "Saturday")


def test_noteless_schedule_covers_every_matching_weekday():
    hub = _dante_hub([{"dayOfWeek": "Friday", "time": "8:30 PM – 1:00 AM"}])
    assert _venue_schedule_covers_event(hub, _scraped("2026-07-31T20:30:00-04:00"), "Friday")


def test_undated_event_falls_back_to_weekday_match():
    hub = _dante_hub(NTH_SCHEDULE)
    assert _venue_schedule_covers_event(hub, {"id": "x", "name": "n", "startDate": ""}, "Friday")


def test_seasonal_schedule_does_not_cover_dates_after_until():
    hub = _dante_hub([
        {
            "dayOfWeek": "Friday",
            "time": "8:30 PM – 10:00 PM",
            "until": "2026-09-30",
        }
    ])
    assert _venue_schedule_covers_event(
        hub, _scraped("2026-09-25T20:30:00-04:00"), "Friday"
    )
    assert not _venue_schedule_covers_event(
        hub, _scraped("2026-10-02T20:30:00-04:00"), "Friday"
    )
