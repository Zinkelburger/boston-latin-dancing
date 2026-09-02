"""Regression tests for re-scrape targeting and verification false alarms.

1. find_duplicate_in must prefer the exact same-ID record over an earlier
   certain-tier lookalike — otherwise a refresh merges into another source's
   copy and the true record never gets its fields backfilled (the Kiz
   Thursday URL bug, 2026-07-14).
2. verify_events must not flag rain-policy boilerplate ("will be canceled in
   the event of inclement weather") as a possible cancellation, and must
   trust JSON-LD eventStatus over page text (the Tito Puente false alarms).
3. publish must never ship nextDateApproximate venue placeholders.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es
import verify_events as ve




def _event(**overrides):
    base = {
        "id": "evt-1",
        "name": "Kiz Thursday",
        "startDate": "2026-08-06T21:30:00-04:00",
        "endDate": "2026-08-07T00:30:00-04:00",
        "location": "44 5th St, Cambridge, MA",
        "lat": 42.37,
        "lng": -71.08,
        "description": "Weekly kizomba social",
        "url": None,
        "styles": ["kizomba"],
        "recurring": True,
        "source": "test",
    }
    base.update(overrides)
    return base


def test_find_duplicate_prefers_exact_id_over_earlier_lookalike(store):
    lookalike = _event(id="other-source-copy", name="Kizz Thursday")
    same_id = _event(id="feed-uid@google.com")
    pool = [lookalike, same_id]

    incoming = _event(id="feed-uid@google.com", url="https://facebook.com/organizer")
    idx, conf = store.find_duplicate_in(incoming, pool)
    assert pool[idx]["id"] == "feed-uid@google.com"
    assert conf == "certain"


def test_refresh_backfills_url_on_the_same_id_record(store):
    lookalike = _event(id="other-source-copy", name="Kizz Thursday")
    same_id = _event(id="feed-uid@google.com")
    store.save_active([lookalike, same_id])

    incoming = _event(id="feed-uid@google.com", url="https://facebook.com/organizer")
    result = store.add_event(incoming)
    assert result["status"] == "duplicate"

    by_id = {e["id"]: e for e in store.load_active()}
    assert by_id["feed-uid@google.com"]["url"] == "https://facebook.com/organizer"
    assert by_id["other-source-copy"]["url"] is None


def test_rain_policy_boilerplate_is_not_a_cancellation():
    html = (
        "<p>In the event of inclement weather, all concerts will be "
        "canceled with no rain dates.</p>"
    )
    assert ve._cancellation_mention(html) is None


def test_real_cancellation_mention_is_flagged():
    html = "<h2>This event has been CANCELLED by the organizer.</h2>"
    m = ve._cancellation_mention(html)
    assert m is not None


def test_jsonld_scheduled_status_overrides_page_text(monkeypatch):
    ld = json.dumps({
        "@type": "Event",
        "name": "Tito Puente Latin Music Series",
        "eventStatus": "https://schema.org/EventScheduled",
    })
    html = (
        f'<script type="application/ld+json">{ld}</script>'
        "<p>This event has been cancelled.</p>"
    )

    class FakeResponse:
        status_code = 200
        text = html

    monkeypatch.setattr(ve.requests, "get", lambda *a, **kw: FakeResponse())
    result = ve.verify_direct(_event(), "https://example.com/event")
    assert result["status"] == "confirmed"


def test_publish_excludes_irregular_venue_placeholders(store, tmp_path, monkeypatch):
    monkeypatch.setattr(es, "VENUES_JSON", tmp_path / "venues.json")
    (tmp_path / "venues.json").write_text(json.dumps([
        {
            "id": "irregular-venue",
            "name": "Irregular Social",
            "location": "1 Somewhere St, Boston, MA",
            "lat": 42.35,
            "lng": -71.06,
            "styles": ["salsa"],
            "nextDateApproximate": True,
            "schedule": [{"dayOfWeek": "Friday", "time": "9:00 PM – 1:00 AM"}],
        },
        {
            "id": "regular-venue",
            "name": "Weekly Social",
            "location": "2 Elsewhere St, Boston, MA",
            "lat": 42.36,
            "lng": -71.07,
            "styles": ["bachata"],
            "schedule": [{"dayOfWeek": "Wednesday", "time": "9:00 PM – 12:00 AM"}],
        },
    ]))
    store.save_active([])
    store.save_archive([])

    store.publish()

    published = json.loads((tmp_path / "events-published.json").read_text())
    names = {e["name"] for e in published}
    assert "Weekly Social" in names

    # Irregular venues publish only as dateless search-only records: findable
    # via search / detail page, but never a pin (no dates, no schedule).
    irregular = [e for e in published if e["name"] == "Irregular Social"]
    assert len(irregular) == 1
    rec = irregular[0]
    assert rec["searchOnly"] is True
    assert rec["startDate"] == ""
    assert rec["endDate"] == ""
    assert "recurrences" not in rec
    assert "schedule" not in rec
