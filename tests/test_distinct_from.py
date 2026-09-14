"""Tests for add_event(distinct_from=[ids]): pre-persisting "different"
verdicts so a lookalike that is a genuinely distinct event is neither queued
for review nor swallowed by a force-merge.

Same tmp-dir isolation pattern as test_block_lifecycle.py, plus
known_duplicates.json (the verdict store distinct_from writes to).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es

# Relative like the other fixtures: hardcoded dates rot into the past and
# add_event then answers skipped_past instead of exercising dedup.
_NY = ZoneInfo("America/New_York")
_FEST = (datetime.now(_NY) + timedelta(days=30)).replace(hour=20, minute=0, second=0, microsecond=0)
_FEST_START = _FEST.isoformat()
_FEST_END = _FEST.replace(hour=23, minute=59).isoformat()
_PRE_START = (_FEST - timedelta(days=1)).replace(hour=21).isoformat()
_PRE_END = _FEST.replace(hour=1, minute=0).isoformat()



def _festival(**overrides):
    base = {
        "id": "bsf-main",
        "name": "Boston Salsa Festival",
        "startDate": _FEST_START,
        "endDate": _FEST_END,
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
        startDate=_PRE_START,
        endDate=_PRE_END,
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
