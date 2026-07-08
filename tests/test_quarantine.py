"""Tests for quarantine mode: unattended ingests must never put brand-new
events on the map — they go to pending.json for the weekly agent review.

Same tmp-dir isolation pattern as test_block_lifecycle.py.
"""

import sys
from pathlib import Path

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


def _event(**overrides):
    base = {
        "id": "evt-q1",
        "name": "Salsa Social at the Docks",
        "startDate": "2026-08-01T20:00:00-04:00",
        "endDate": "2026-08-01T23:00:00-04:00",
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
