"""Tests for dedup precedence: venue hubs beat manual beat scraped."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from event_store import (
    collapse_recurring_series,
    dedup_confidence,
    deduplicate,
    merge_event,
    pick_winner,
    source_rank,
)


def _venue_hub(**overrides):
    base = {
        "id": "havana-club",
        "name": "Havana Club",
        "startDate": "2026-05-25T20:00:00-04:00",
        "endDate": "2026-05-26T00:30:00-04:00",
        "location": "288 Green St, Cambridge, MA 02139",
        "lat": 42.3649,
        "lng": -71.1035,
        "description": "Curated venue description.",
        "url": "https://havanaclubsalsa.com",
        "styles": ["bachata", "salsa"],
        "cost": "$15",
        "source": "recurring-venues",
        "schedule": [{"dayOfWeek": "Monday", "time": "8:00 PM – 12:30 AM"}],
        "recurring": True,
    }
    base.update(overrides)
    return base


def _scraped_night(**overrides):
    base = {
        "id": "4BACCA44-811C-4F7E-99D8-7AC8DA46D5CC",
        "name": "Bachata Sensual Mondays @ Havana Club",
        "startDate": "2026-05-25T20:00:00-04:00",
        "endDate": "2026-05-26T00:30:00-04:00",
        "location": "Havana Club\n288 Green St, Cambridge, MA",
        "lat": 42.3649,
        "lng": -71.1035,
        "description": "X" * 500,
        "url": "https://havanaclubsalsa.com",
        "styles": ["bachata"],
        "cost": "$15",
        "source": "sensualeros-boston",
        "recurring": True,
    }
    base.update(overrides)
    return base


class TestSourcePrecedence:
    def test_venue_hub_beats_scraped_rank(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        assert source_rank(venue) < source_rank(scraped)

    def test_manual_beats_scraped(self):
        manual = _scraped_night(source="manual", description="manual entry")
        scraped = _scraped_night()
        assert source_rank(manual) < source_rank(scraped)

    def test_pick_winner_prefers_venue_over_longer_scraped_description(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        winner, loser = pick_winner(venue, scraped)
        assert winner["id"] == "havana-club"
        assert loser["id"] == scraped["id"]


class TestMergeEvent:
    def test_venue_description_not_replaced_by_longer_scraped(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        merged = merge_event(venue, scraped)
        assert merged["description"] == "Curated venue description."
        assert merged.get("schedule")

    def test_venue_wins_even_when_scraped_is_first_arg(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        merged = merge_event(scraped, venue)
        assert merged["id"] == "havana-club"
        assert merged["description"] == "Curated venue description."
        assert merged.get("schedule")

    def test_gap_fill_from_loser_when_winner_lacks_field(self):
        venue = _venue_hub(url=None)
        scraped = _scraped_night(url="https://example.com/event")
        merged = merge_event(venue, scraped)
        assert merged["url"] == "https://example.com/event"


class TestDedupConfidence:
    def test_venue_hub_not_duplicate_of_scraped_night(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        assert dedup_confidence(venue, scraped) is None

    def test_same_id_still_certain(self):
        a = _venue_hub()
        b = _venue_hub(source="")
        assert dedup_confidence(a, b) == "certain"


class TestDeduplicate:
    def test_venue_and_scraped_night_both_kept(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        result = deduplicate([scraped, venue])
        ids = {e["id"] for e in result}
        assert "havana-club" in ids
        assert scraped["id"] in ids

    def test_same_id_merges_into_venue(self):
        expanded = _venue_hub(source="recurring-venues")
        active = _venue_hub(source="", description="Active store copy")
        result = deduplicate([active, expanded])
        assert len(result) == 1
        assert result[0]["id"] == "havana-club"
        assert result[0].get("schedule")


class TestCollapseRecurringSeries:
    def test_venue_hub_not_collapsed_with_scraped_night(self):
        venue = _venue_hub()
        scraped = _scraped_night()
        result = collapse_recurring_series([venue, scraped])
        assert len(result) == 2
        assert any(e["id"] == "havana-club" for e in result)

    def test_collapse_winner_by_source_not_description_length(self):
        short = {
            "id": "a",
            "name": "Havana Bachata Thursdays",
            "location": "288 Green St",
            "startDate": "2026-05-21T20:45:00-04:00",
            "description": "short",
            "source": "manual",
        }
        long_desc = {
            "id": "b",
            "name": "Havana Bachata Thursdays",
            "location": "288 Green St",
            "startDate": "2026-05-28T20:45:00-04:00",
            "description": "x" * 500,
            "source": "sensualeros-boston",
        }
        result = collapse_recurring_series([long_desc, short])
        assert len(result) == 1
        assert result[0]["source"] == "manual"
        assert result[0]["description"] == "short"
