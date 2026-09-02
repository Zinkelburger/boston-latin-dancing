"""Approving a dedup pair whose candidate is already archived must merge, not duplicate.

The J&L "Boston Salsa Fest" promo row queued against the archived "Boston Salsa
Festival, 2026". approve_pending() lands its event with add_event(force=True),
and force skips add_event's archive-match branch, so the approval appended a
second copy to active — which archive_past_events() then filed beside the
original. One past festival, two archive rows, and (since publish() ships the
archive for SEO and MapView makes archived events searchable) two ghosts under
two different names, the second at a downtown coordinate for a Waltham event.

Same tmp-dir isolation pattern as test_distinct_from.py.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es




NY = ZoneInfo("America/New_York")


def _day(days_from_now: int) -> str:
    """8 PM Boston, `days_from_now` days out. Negative goes backwards.

    Relative, like the other fixtures: hardcoded dates rot into the past and
    take the still-upcoming assertions down with them.
    """
    return (datetime.now(NY) + timedelta(days=days_from_now)).replace(
        hour=20, minute=0, second=0, microsecond=0
    ).isoformat()


def _festival(day: int) -> dict:
    return {
        "id": "bsf-2026@dance-calendar",
        "name": "Boston Salsa Festival, 2026",
        "startDate": _day(day),
        "endDate": _day(day),
        "location": "70 Third Avenue, Waltham, MA 02451",
        "lat": 42.3947033,
        "lng": -71.2599412,
        "description": "Workshops, performances, live music.",
        "url": "https://www.eventbrite.com/e/boston-salsa-festival-2026",
        "styles": ["salsa"],
        "recurring": False,
        "source": "beatrice-calendar",
    }


def _promo_row(day: int) -> dict:
    """The J&L shape: same festival, vaguer name, no real venue."""
    return {
        "id": "jandl-boston-salsa-fest",
        "name": "Boston Salsa Fest",
        "startDate": _day(day),
        "endDate": _day(day),
        "location": "Boston, MA",
        "lat": 42.3588336,
        "lng": -71.0578303,
        "description": "Listed on J&L's events bar. J&L promo code: jnl26.",
        "url": "https://jandldancestudio.com/events",
        "styles": ["salsa"],
        "recurring": False,
        "source": "jandl-events",
        "_dedup_candidate_of": "bsf-2026@dance-calendar",
        "_dedup_confidence": "review",
        "_dedup_reason": "substring_name+same_day",
    }


def _queue(store, event: dict) -> None:
    store.save_pending(store.load_pending() + [event])


def test_past_candidate_folds_into_the_archived_row(store):
    store.save_archive([_festival(-7)])
    _queue(store, _promo_row(-7))

    result = store.approve_pending("jandl-boston-salsa-fest", force=True)

    assert result["status"] == "merged_into_archive"
    archive = store.load_archive()
    assert len(archive) == 1, "the approval added a second archive row instead of merging"
    assert archive[0]["id"] == "bsf-2026@dance-calendar"
    assert store.load_active() == [], "a past event must not be put back on the map"
    assert store.load_pending() == []


def test_the_merge_keeps_the_candidate_url(store):
    store.save_archive([_festival(-7)])
    _queue(store, _promo_row(-7))

    store.approve_pending("jandl-boston-salsa-fest", force=True)

    merged = store.load_archive()[0]
    assert "https://jandldancestudio.com/events" in merged.get("urls", [])
    assert merged["location"] == "70 Third Avenue, Waltham, MA 02451", (
        "the better-ranked source's real venue must survive the merge"
    )


def test_upcoming_candidate_comes_back_to_active(store):
    """An archived candidate whose merged dates are still ahead belongs on the map."""
    store.save_archive([_festival(7)])
    _queue(store, _promo_row(7))

    result = store.approve_pending("jandl-boston-salsa-fest", force=True)

    assert result["status"] == "reactivated"
    assert store.load_archive() == []
    active = store.load_active()
    assert [e["id"] for e in active] == ["bsf-2026@dance-calendar"]
    assert active[0].get("reactivatedAt")


def test_verdict_is_persisted_so_it_never_queues_again(store):
    store.save_archive([_festival(-7)])
    _queue(store, _promo_row(-7))

    store.approve_pending("jandl-boston-salsa-fest", force=True)

    verdicts = store.list_known_duplicates()
    assert any(
        v["verdict"] == "same"
        and {v["id_a"], v["id_b"]} == {"jandl-boston-salsa-fest", "bsf-2026@dance-calendar"}
        for v in verdicts
    )


def test_active_candidate_still_takes_the_normal_path(store):
    """The archive detour must not intercept a pair whose candidate is live."""
    store.save_active([_festival(7)])
    _queue(store, _promo_row(7))

    result = store.approve_pending("jandl-boston-salsa-fest", force=True)

    # add_event's own dedup handles it: the verdict persisted a moment earlier
    # makes the pair certain-tier, so it merges in place rather than queueing.
    assert result["status"] == "duplicate"
    active = store.load_active()
    assert [e["id"] for e in active] == ["bsf-2026@dance-calendar"]
    assert "https://jandldancestudio.com/events" in active[0].get("urls", [])
    assert store.load_archive() == []


def test_candidate_missing_everywhere_is_added_normally(store):
    """A dangling _dedup_candidate_of must not swallow the approval."""
    _queue(store, _promo_row(7))

    result = store.approve_pending("jandl-boston-salsa-fest", force=True)

    assert result["status"] == "added"
    assert [e["id"] for e in store.load_active()] == ["jandl-boston-salsa-fest"]
