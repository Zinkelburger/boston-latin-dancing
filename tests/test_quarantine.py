"""Tests for quarantine mode: unattended ingests must never put brand-new
events on the map — they go to pending.json for the weekly agent review.

Same tmp-dir isolation pattern as test_block_lifecycle.py.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es


@pytest.fixture
def store(tmp_path, monkeypatch):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    scraped_dir = tmp_path / "scraped"
    scraped_dir.mkdir()
    monkeypatch.setattr(es, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(es, "ACTIVE_JSON", events_dir / "active.json")
    monkeypatch.setattr(es, "ARCHIVE_JSON", events_dir / "archive.json")
    monkeypatch.setattr(es, "PENDING_JSON", events_dir / "pending.json")
    monkeypatch.setattr(es, "REJECTED_JSON", events_dir / "rejected.json")
    monkeypatch.setattr(es, "BLOCKED_JSON", events_dir / "blocked.json")
    monkeypatch.setattr(es, "CHANGELOG", events_dir / "changelog.jsonl")
    monkeypatch.setattr(es, "SCRAPED_DIR", scraped_dir)
    return es


NY = ZoneInfo("America/New_York")


def _at(days_from_now: int) -> datetime:
    """8 PM Boston, `days_from_now` from today.

    Fixture dates must be relative: hardcoded ones silently rot into the past,
    where add_event returns skipped_past and eight unrelated tests start failing
    for a reason that has nothing to do with what they cover.
    """
    return (datetime.now(NY) + timedelta(days=days_from_now)).replace(
        hour=20, minute=0, second=0, microsecond=0)


def _event(**overrides):
    start = _at(14)
    base = {
        "id": "evt-q1",
        "name": "Salsa Social at the Docks",
        "startDate": start.isoformat(),
        "endDate": (start + timedelta(hours=3)).isoformat(),
        "location": "1 Pier Rd, Boston, MA",
        "lat": 42.36,
        "lng": -71.05,
        "description": "Outdoor salsa social",
        "url": "https://example.com/social",
        "styles": ["salsa"],
        "recurring": False,
        "source": "test",
    }
    base.update(overrides)
    return base


def test_quarantine_routes_new_event_to_pending(store):
    result = store.add_event(_event(), quarantine_new=True)
    assert result["status"] == "quarantined_new"
    assert store.load_active() == []
    pending = store.load_pending()
    assert len(pending) == 1
    assert pending[0]["_quarantined_new"] is True


def test_requarantine_updates_in_place(store):
    store.add_event(_event(), quarantine_new=True)
    first_seen = store.load_pending()[0]["_quarantined_at"]
    store.add_event(_event(description="updated blurb"), quarantine_new=True)
    pending = store.load_pending()
    assert len(pending) == 1
    assert pending[0]["description"] == "updated blurb"
    assert pending[0]["_quarantined_at"] == first_seen


def test_quarantine_still_merges_certain_duplicates(store):
    store.add_event(_event())  # normal add -> active
    result = store.add_event(_event(description="fresher"), quarantine_new=True)
    assert result["status"] == "duplicate"  # certain merge refreshed active
    assert len(store.load_active()) == 1
    assert store.load_pending() == []


def test_approve_quarantined_moves_to_active_and_strips_markers(store):
    store.add_event(_event(), quarantine_new=True)
    result = store.approve_pending("evt-q1")
    assert result["status"] == "added"
    assert store.load_pending() == []
    active = store.load_active()
    assert len(active) == 1
    assert "_quarantined_new" not in active[0]
    assert "_quarantined_at" not in active[0]


def test_non_latin_event_is_queued_for_review(store):
    # An event with no Latin keywords goes to rejected.json, where a reviewer
    # can rescue a real social with an odd title or block a recurring false
    # hit for good. Never onto the map, and never silently dropped.
    ev = _event(name="Community Festival", description="a parade", styles=["other"])
    result = store.add_event(dict(ev))
    assert result["status"] == "rejected_non_latin"
    assert store.load_active() == []
    assert store.load_pending() == []
    rejected = store.load_rejected()
    assert [r["id"] for r in rejected] == ["evt-q1"]
    assert rejected[0]["_review_type"] == "non_latin"
    # A re-scrape refreshes the queued row instead of adding a second one.
    store.add_event(dict(ev, description="a parade, now with floats"))
    rejected = store.load_rejected()
    assert len(rejected) == 1
    assert rejected[0]["description"] == "a parade, now with floats"


def test_latin_keyword_event_still_passes(store):
    # 'other'-tagged but the text mentions a Latin term -> kept.
    ev = _event(name="Cumbia Night", description="live cumbia band", styles=["other"])
    assert store.add_event(dict(ev))["status"] == "added"
    assert len(store.load_active()) == 1


def test_rescrape_of_approved_event_merges_instead_of_redropping(store):
    # A real Latin event lands in active (it has a style)...
    ev = _event(id="evt-x", name="Salsa Social")
    assert store.add_event(dict(ev))["status"] == "added"
    # ...and a later keyword-less re-scrape of the same id must merge, not
    # re-drop it (the "already approved" fallback keeps it on the map).
    stripped = _event(id="evt-x", name="Community Festival",
                      description="a parade", styles=["other"])
    again = store.add_event(dict(stripped), quarantine_new=True)
    assert again["status"] == "duplicate"
    assert len(store.load_active()) == 1


def test_trusted_source_bypasses_keyword_check(store, monkeypatch):
    # Curated Latin sources are trusted: a keyword-less event still gets in.
    monkeypatch.setattr(es, "_trusted_latin_sources", lambda: {"beatrice-calendar"})
    ev = _event(name="Thursday Night Social", description="weekly social",
                styles=["other"], source="beatrice-calendar")
    assert store.add_event(dict(ev))["status"] == "added"
    assert len(store.load_active()) == 1


def test_stale_scrape_does_not_reactivate_archived_past_event(store):
    gone = _at(-30)
    past = _event(id="evt-past", startDate=gone.isoformat(),
                  endDate=(gone + timedelta(hours=3)).isoformat())
    # force=True to seed: unforced adds of already-past events are skipped.
    store.add_event(dict(past), force=True)
    assert len(store.archive_past_events()) == 1
    # A stale scraped file re-lists the same past date: must stay archived.
    result = store.add_event(dict(past), quarantine_new=True)
    assert result["status"] == "duplicate"
    assert store.load_active() == []
    assert len(store.load_archive()) == 1


def test_archiving_the_same_id_twice_does_not_duplicate_the_archive(store):
    """A re-scrape that fails to match the archived copy (a changed venue
    string is enough) lands a fresh active record with the same id. Archiving
    it again used to append a second, byte-identical archive entry, and
    publish() ships the archive verbatim — so the site rendered the same past
    event twice."""
    gone = _at(-30)
    past = _event(id="evt-past", startDate=gone.isoformat(),
                  endDate=(gone + timedelta(hours=3)).isoformat())
    store.add_event(dict(past), force=True)
    assert len(store.archive_past_events()) == 1

    # Same id back in active (however it got there), archived a second time.
    store.save_active([dict(past)])
    assert len(store.archive_past_events()) == 1
    archive = store.load_archive()
    assert [e["id"] for e in archive] == ["evt-past"]


def test_brand_new_past_event_is_skipped(store):
    gone = _at(-30)
    past = _event(id="evt-old", startDate=gone.isoformat(),
                  endDate=(gone + timedelta(hours=3)).isoformat())
    result = store.add_event(dict(past), quarantine_new=True)
    assert result["status"] == "skipped_past"
    assert store.load_active() == []
    assert store.load_pending() == []


def test_shared_url_different_dates_stay_separate_events(store):
    # Two occurrences of a series that share one organizer URL must not merge.
    d1 = _at(14)
    d2 = _at(35)
    jul = _event(id="series-0717", startDate=d1.isoformat(),
                 endDate=(d1 + timedelta(hours=3)).isoformat(), url="https://org.example/socials")
    aug = _event(id="series-0807", startDate=d2.isoformat(),
                 endDate=(d2 + timedelta(hours=3)).isoformat(), url="https://org.example/socials")
    assert store.add_event(dict(jul))["status"] == "added"
    result = store.add_event(dict(aug))
    assert result["status"] == "added", result
    assert len(store.load_active()) == 2
    # But a same-date re-scrape with the shared URL still merges.
    again = store.add_event(dict(jul, id="series-0717-alt"))
    assert again["status"] == "duplicate"
    assert len(store.load_active()) == 2


def test_block_finds_quarantined_event(store):
    store.add_event(_event(), quarantine_new=True)
    result = store.block_event("evt-q1", "not_dance", "concert, no social dancing")
    assert result["status"] == "blocked"
    assert store.load_pending() == []
    assert {b["id"] for b in store.load_blocked()} == {"evt-q1"}
    # And a re-scrape can't bring it back, even quarantined.
    again = store.add_event(_event(), quarantine_new=True)
    assert again["status"] == "blocked"
    assert store.load_pending() == []
