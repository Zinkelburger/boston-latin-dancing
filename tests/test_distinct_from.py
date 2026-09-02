"""Tests for add_event(distinct_from=[ids]): pre-persisting "different"
verdicts so a lookalike that is a genuinely distinct event is neither queued
for review nor swallowed by a force-merge.

Same tmp-dir isolation pattern as test_block_lifecycle.py, plus
known_duplicates.json (the verdict store distinct_from writes to).
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
    monkeypatch.setattr(es, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(es, "ACTIVE_JSON", events_dir / "active.json")
    monkeypatch.setattr(es, "ARCHIVE_JSON", events_dir / "archive.json")
    monkeypatch.setattr(es, "PENDING_JSON", events_dir / "pending.json")
    monkeypatch.setattr(es, "REJECTED_JSON", events_dir / "rejected.json")
    monkeypatch.setattr(es, "BLOCKED_JSON", events_dir / "blocked.json")
    monkeypatch.setattr(es, "CHANGELOG", events_dir / "changelog.jsonl")
    monkeypatch.setattr(es, "KNOWN_DUPLICATES_JSON", tmp_path / "known_duplicates.json")
    return es


def _festival(**overrides):
    base = {
        "id": "bsf-main",
        "name": "Boston Salsa Festival",
        "startDate": "2026-09-12T20:00:00-04:00",
        "endDate": "2026-09-12T23:59:00-04:00",
        "location": "100 Main St, Boston, MA",
        "lat": 42.36,
        "lng": -71.06,
        "description": "The big one",
        "url": "https://example.com/bsf",
        "styles": ["salsa"],
        "recurring": False,
        "source": "test",
    }
    base.update(overrides)
    return base


def _preparty(**overrides):
    # Substring name match within 24h at a different location -> review tier
    # (the exact shape of the real Pre-Party/BSF near-miss).
    return _festival(
        id="bsf-preparty",
        name="Pre-Party: Boston Salsa Festival",
        startDate="2026-09-11T21:00:00-04:00",
        endDate="2026-09-12T01:00:00-04:00",
        location="5 River St, Cambridge, MA",
        lat=42.37,
        lng=-71.10,
        url="https://example.com/bsf-preparty",
        **overrides,
    )


def test_lookalike_pair_is_review_tier(store):
    # Guard the premise: if dedup rules change and this pair stops being a
    # review match, the tests below stop exercising distinct_from.
    assert store.dedup_confidence(_festival(), _preparty()) == "review"


def test_force_without_distinct_from_still_merges(store):
    # The documented sharp edge, unchanged: force alone swallows the lookalike.
    store.add_event(_festival())
    result = store.add_event(_preparty(), force=True)
    assert result["status"] == "merged"
    assert len(store.load_active()) == 1


def test_force_with_distinct_from_adds_separately(store):
    store.add_event(_festival())
    result = store.add_event(_preparty(), distinct_from=["bsf-main"], force=True)
    assert result["status"] == "added"
    assert {e["id"] for e in store.load_active()} == {"bsf-main", "bsf-preparty"}
    verdicts = store.list_known_duplicates()
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "different"
    assert {verdicts[0]["id_a"], verdicts[0]["id_b"]} == {"bsf-main", "bsf-preparty"}


def test_unforced_distinct_from_skips_review_queue(store):
    store.add_event(_festival())
    result = store.add_event(_preparty(), distinct_from=["bsf-main"])
    assert result["status"] == "added"
    assert store.load_pending() == []
    assert len(store.load_active()) == 2


def test_rescrape_after_distinct_from_merges_with_itself(store):
    store.add_event(_festival())
    store.add_event(_preparty(), distinct_from=["bsf-main"], force=True)
    again = store.add_event(_preparty())
    assert again["status"] == "duplicate"  # certain self-merge, not a new pair
    assert again["existing"]["id"] == "bsf-preparty"  # merged into itself, not bsf-main
    assert len(store.load_active()) == 2
    assert store.load_pending() == []


def test_self_id_in_distinct_from_is_ignored(store):
    # A self-pair verdict would suppress the event's own certain-tier merge
    # forever; it must never be written.
    store.add_event(_preparty(), distinct_from=["bsf-preparty"])
    assert store.list_known_duplicates() == []
    again = store.add_event(_preparty(description="update"))
    assert again["status"] == "duplicate"
    assert len(store.load_active()) == 1
