"""A block must survive the source re-minting the event's id.

Wix (`nlf-events-<slug>-<date>-<time>`) and Eventbrite (`eb-<numeric>`) issue a
fresh id for every occurrence of a weekly series. Blocking matched on id alone,
so a blocked weekly class reappeared in the pending queue the following week,
forever. Blocking also matches on normalized name + venue.
"""

import json
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


def _weekly_class(week_id, **overrides):
    base = {
        "id": f"nlf-events-intermediate-bachata-w-tina-{week_id}",
        "name": "Intermediate Bachata w/ Tina",
        "startDate": "2099-07-30T20:45:00-04:00",
        "location": "Havana Club, 288 Green St, Cambridge, MA 02139, USA",
        "lat": 42.3654,
        "lng": -71.1030,
        "styles": ["bachata"],
        "source": "nlf-events",
    }
    base.update(overrides)
    return base


class TestBlockSurvivesNewId:
    def test_next_weeks_occurrence_is_blocked(self, store):
        store.save_active([_weekly_class("2026-07-30-20-45")])
        assert store.block_event(
            "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45",
            "class_only")["status"] == "blocked"

        # Same series, next week, brand-new id straight from the scraper.
        result = store.add_event(_weekly_class("2026-08-06-20-45"))
        assert result["status"] == "blocked"
        assert store.load_active() == []
        assert store.load_pending() == []

    def test_block_record_stores_the_key(self, store):
        store.save_active([_weekly_class("2026-07-30-20-45")])
        store.block_event(
            "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45", "class_only")
        assert store.load_blocked()[0]["block_key"]

    def test_legacy_block_record_without_key_still_works(self, store):
        """Records written before block_key existed derive it on read."""
        store.save_blocked([{
            "id": "old-id",
            "name": "Intermediate Bachata w/ Tina",
            "location": "Havana Club, 288 Green St, Cambridge, MA 02139, USA",
            "blocked_category": "class_only",
        }])
        assert store.add_event(_weekly_class("2026-08-06-20-45"))["status"] == "blocked"

    def test_punctuation_differences_in_address_still_match(self, store):
        """The real blocklist stores "St, 101 Union" where the scraper emits
        "St\\n101 Union". Whitespace and punctuation must not defeat the block."""
        store.save_blocked([{
            "id": "eb-1993330109015",
            "name": "Newton Adult Latin Dance Class — Beginner Rumba",
            "location": "101 Union St, 101 Union Street, Newton, MA 02459, Newton, MA",
            "blocked_category": "class_only",
        }])
        rescraped = _weekly_class(
            "irrelevant",
            id="eb-1995250022528",
            name="Newton Adult Latin Dance Class — Beginner Rumba",
            location="101 Union St\n101 Union Street, Newton, MA 02459, Newton, MA",
            lat=42.3168, lng=-71.2075)
        assert store.add_event(rescraped)["status"] == "blocked"

    def test_different_event_at_same_venue_not_blocked(self, store):
        """The key is name+venue — blocking a class must not block the social."""
        store.save_active([_weekly_class("2026-07-30-20-45")])
        store.block_event(
            "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45", "class_only")
        social = _weekly_class("2026-08-06-21-00",
                               id="havana-bachata-thursdays",
                               name="Havana Bachata Thursdays")
        assert store.add_event(social)["status"] == "added"

    def test_same_name_different_venue_not_blocked(self, store):
        store.save_active([_weekly_class("2026-07-30-20-45")])
        store.block_event(
            "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45", "class_only")
        elsewhere = _weekly_class("2026-08-06-20-45",
                                  id="other-venue-class",
                                  location="Ryles Jazz Club, 212 Hampshire St, Cambridge, MA")
        assert store.add_event(elsewhere)["status"] == "added"

    def test_force_still_bypasses_key_block(self, store):
        """Admin approval must not be defeated by the new matcher."""
        store.save_active([_weekly_class("2026-07-30-20-45")])
        store.block_event(
            "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45", "class_only")
        assert store.add_event(
            _weekly_class("2026-08-06-20-45"), force=True)["status"] == "added"

    def test_ingest_counts_the_re_minted_occurrence_as_blocked(self, store):
        store.save_active([_weekly_class("2026-07-30-20-45")])
        store.block_event(
            "nlf-events-intermediate-bachata-w-tina-2026-07-30-20-45", "class_only")
        (store.SCRAPED_DIR / "nlf-events.json").write_text(
            json.dumps([_weekly_class("2026-08-06-20-45")]))
        result = store.ingest_scraped(source_id="nlf-events", quarantine_new=True)
        assert result["blocked"] == 1
        assert result.get("quarantined_new", 0) == 0
