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


def test_browser_attestation_is_fresh_fingerprint_bound_and_reported(store, tmp_path, monkeypatch):
    event = _event(url="https://www.facebook.com/events/123/")
    store.save_active([event])
    monkeypatch.setattr(ve, "REPORT_PATH", tmp_path / "verification-report.json")

    result = ve.attest_event(
        "evt-1",
        "https://www.facebook.com/events/123/",
        notes="Upcoming card and detail page checked",
        observed_start=event["startDate"],
        observed_location=event["location"],
    )
    assert result["status"] == "attested"
    saved = store.load_active()[0]
    verified = ve.verify_event(saved)
    assert verified["status"] == "confirmed"
    assert verified["method"] == "browser_attestation"
    assert json.loads(ve.REPORT_PATH.read_text())[0]["event_id"] == "evt-1"

    changed = dict(saved, startDate="2026-08-13T21:30:00-04:00")
    assert ve._fresh_attestation(changed) is None
    expired = dict(saved)
    expired["_verification_attestation"] = dict(
        saved["_verification_attestation"],
        expires_at="2020-01-01T00:00:00+00:00",
    )
    assert ve._fresh_attestation(expired) is None


def test_browser_attestation_validates_inputs(store, tmp_path, monkeypatch):
    store.save_active([_event()])
    monkeypatch.setattr(ve, "REPORT_PATH", tmp_path / "verification-report.json")
    assert ve.attest_event("missing", "https://example.com")["status"] == "not_found"
    assert ve.attest_event("evt-1", "not a url")["status"] == "invalid"
    assert ve.attest_event("evt-1", "https://example.com", status="maybe")["status"] == "invalid"
    assert ve.attest_event("evt-1", "https://example.com", valid_days=0)["status"] == "invalid"
    assert ve.attest_event("evt-1", "https://example.com", observed_start="tomorrow")["status"] == "invalid"


def test_targeted_verification_preserves_other_report_rows(store, tmp_path, monkeypatch):
    first = _event(id="evt-1", url="https://example.com/one")
    second = _event(id="evt-2", url="https://example.com/two")
    store.save_active([first, second])
    monkeypatch.setattr(ve, "REPORT_PATH", tmp_path / "verification-report.json")
    ve.write_json(ve.REPORT_PATH, [{
        "event_id": "evt-2",
        "event_name": "Second",
        "status": "confirmed",
        "verified_at": "2026-09-01T00:00:00+00:00",
    }])
    monkeypatch.setattr(ve, "verify_event", lambda event: {
        "event_id": event["id"],
        "event_name": event["name"],
        "status": "confirmed",
        "verified_at": "2026-09-04T00:00:00+00:00",
        "source_url": event["url"],
    })

    report = ve.verify_all(event_id="evt-1")

    assert {row["event_id"] for row in report} == {"evt-1", "evt-2"}
    assert {row["event_id"] for row in json.loads(ve.REPORT_PATH.read_text())} == {"evt-1", "evt-2"}


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
