"""A time the reviewer corrects must stay corrected across re-scrapes.

A same-id re-scrape refreshes startDate/endDate from the source, which is right
when the organizer moves an event and wrong when the source itself is wrong.
Eventbrite listed the ZUMIX Latin Dance Fundraiser at 6–11 AM; the weekly
review fixed it to 6–11 PM, and the next refresh put the 6 AM start straight
back on the map. An edited time is recorded like a location override and wins
the merge until a reviewer edits it again.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es


SOURCE_START = "2026-10-24T06:00:00-04:00"
SOURCE_END = "2026-10-24T11:00:00-04:00"
FIXED_START = "2026-10-24T18:00:00-04:00"
FIXED_END = "2026-10-24T23:00:00-04:00"


def _event(**over) -> dict:
    ev = {
        "id": "eb-2000672130198",
        "name": "Latin Dance Fundraiser with DJ Johnny Giraldo",
        "startDate": SOURCE_START,
        "endDate": SOURCE_END,
        "dayOfWeek": "Saturday",
        "location": "ZUMIX, 260 Sumner Street, Boston, MA 02128",
        "lat": 42.3686,
        "lng": -71.0379,
        "description": "Latin dance fundraiser.",
        "url": "https://www.eventbrite.com/e/latin-dance-fundraiser-with-dj-johnny-giraldo-tickets-2000672130198",
        "styles": ["salsa"],
        "cost": "$20",
        "source": "eventbrite-boston-latin",
    }
    ev.update(over)
    return ev


def test_corrected_time_survives_a_rescrape(store):
    store.save_active([_event()])
    store.edit_event("eb-2000672130198", {"startDate": FIXED_START, "endDate": FIXED_END})

    store.add_event(_event(), skip_latin_check=True)   # source still says 6 AM

    stored = store.load_active()[0]
    assert stored["startDate"] == FIXED_START
    assert stored["endDate"] == FIXED_END


def test_override_is_recorded_but_never_published(store):
    store.save_active([_event()])
    store.edit_event("eb-2000672130198", {"startDate": FIXED_START, "endDate": FIXED_END})

    stored = store.load_active()[0]
    assert stored["_time_override"] == {"startDate": FIXED_START, "endDate": FIXED_END}

    store._strip_internal_fields(stored, {})
    assert "_time_override" not in stored


def test_unedited_event_still_takes_the_source_time(store):
    store.save_active([_event()])

    moved = "2026-10-25T18:00:00-04:00"
    store.add_event(_event(startDate=moved, dayOfWeek="Sunday"), skip_latin_check=True)

    stored = store.load_active()[0]
    assert stored["startDate"] == moved
    assert stored["dayOfWeek"] == "Sunday"


def test_merge_keeps_override_whichever_side_wins():
    edited = _event(startDate=FIXED_START, endDate=FIXED_END,
                    _time_override={"startDate": FIXED_START, "endDate": FIXED_END})
    fresh = _event()

    for a, b in ((edited, fresh), (fresh, edited)):
        merged = es.merge_event(a, b)
        assert merged["startDate"] == FIXED_START
        assert merged["endDate"] == FIXED_END
        assert merged["_time_override"] == {"startDate": FIXED_START, "endDate": FIXED_END}
