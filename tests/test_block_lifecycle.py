"""Tests for the blocklist + out-of-area geo-fence and their interaction with
the approval paths.

These exercise the on-disk lifecycle, so the fixture redirects every store
path to a throwaway tmp dir. Events always carry lat/lng so add_event never
falls back to the network geocoder.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es




# Boston ~ (42.36, -71.06); Miami ~ (25.8, -80.2) is well past the 50km radius.
def _event(**overrides):
    base = {
        "id": "evt-1",
        "name": "Salsa Social",
        "startDate": "2099-07-01T20:00:00-04:00",
        "location": "Boston, MA",
        "lat": 42.36,
        "lng": -71.06,
        "styles": ["salsa"],
        "source": "test-source",
    }
    base.update(overrides)
    return base


def _in_active(store, event_id):
    return any(e["id"] == event_id for e in store.load_active())


class TestGeoFence:
    def test_in_area_event_added(self, store):
        assert store.add_event(_event())["status"] == "added"
        assert _in_active(store, "evt-1")

    def test_out_of_area_event_rejected(self, store):
        result = store.add_event(_event(lat=25.8, lng=-80.2, location="Miami, FL"))
        assert result["status"] == "rejected_out_of_area"
        assert not _in_active(store, "evt-1")

    def test_event_without_coords_is_not_out_of_area(self, store):
        # No coordinates -> can't be judged, must pass the guard (helper is pure,
        # so this avoids the network geocoder in add_event).
        assert store._is_out_of_area(_event(lat=None, lng=None)) is False

    def test_rescrape_flipping_out_of_area_purges_stale_active_copy(self, store):
        store.save_active([_event()])
        result = store.add_event(_event(lat=25.8, lng=-80.2, location="Miami, FL"))
        assert result["status"] == "rejected_out_of_area"
        assert not _in_active(store, "evt-1")


class TestBlockLifecycle:
    def test_block_from_active_moves_to_blocklist(self, store):
        store.save_active([_event()])
        result = store.block_event("evt-1", "defunct", "series ended")
        assert result["status"] == "blocked"
        assert not _in_active(store, "evt-1")
        blocked = store.load_blocked()
        assert [b["id"] for b in blocked] == ["evt-1"]
        assert blocked[0]["blocked_category"] == "defunct"

    def test_blocked_event_not_re_added_on_scrape(self, store):
        store.save_blocked([{"id": "evt-1", "blocked_category": "defunct"}])
        result = store.add_event(_event())
        assert result["status"] == "blocked"
        assert not _in_active(store, "evt-1")

    def test_blocked_ids_param_avoids_reload(self, store):
        # blocked.json is empty on disk, but the passed-in set still blocks.
        result = store.add_event(_event(), blocked_ids={"evt-1"})
        assert result["status"] == "blocked"

    def test_unblock_removes_from_blocklist(self, store):
        store.save_blocked([{"id": "evt-1", "blocked_category": "defunct"}])
        assert store.unblock_event("evt-1")["status"] == "unblocked"
        assert store.load_blocked() == []

    def test_block_invalid_category_is_error(self, store):
        store.save_active([_event()])
        result = store.block_event("evt-1", "bogus")
        assert result["status"] == "error"
        # The event must not be lost from active on a rejected category.
        assert _in_active(store, "evt-1")

    def test_block_missing_event_not_found(self, store):
        assert store.block_event("nope", "defunct")["status"] == "not_found"


class TestApprovalBypassesGuards:
    """force=True (admin approval) must bypass the ingest-time guards, or the
    event is lost: it's already been popped from its queue before add_event."""

    def test_force_bypasses_geofence(self, store):
        result = store.add_event(_event(lat=25.8, lng=-80.2), force=True)
        assert result["status"] == "added"

    def test_force_bypasses_blocklist(self, store):
        store.save_blocked([{"id": "evt-1", "blocked_category": "defunct"}])
        assert store.add_event(_event(), force=True)["status"] == "added"

    def test_approve_pending_out_of_area_not_lost(self, store):
        store.save_pending([_event(lat=25.8, lng=-80.2, location="Miami, FL")])
        store.approve_pending("evt-1")
        assert _in_active(store, "evt-1")
        assert store.load_pending() == []

    def test_approve_rejected_out_of_area_not_lost(self, store):
        store.save_rejected([_event(lat=25.8, lng=-80.2, _rejected_reason="x")])
        store.approve_rejected("evt-1")
        assert _in_active(store, "evt-1")
        assert store.load_rejected() == []


class TestIngestCounters:
    def test_counts_blocked_and_out_of_area(self, store):
        import json

        store.save_blocked([{"id": "evt-blocked", "blocked_category": "defunct"}])
        scraped = [
            _event(id="evt-good"),
            _event(id="evt-blocked"),
            _event(id="evt-far", lat=25.8, lng=-80.2, location="Miami, FL"),
        ]
        (store.SCRAPED_DIR / "test.json").write_text(json.dumps(scraped))

        result = store.ingest_scraped(source_id="test")
        assert result["added"] == 1
        assert result["blocked"] == 1
        assert result["rejected_out_of_area"] == 1
