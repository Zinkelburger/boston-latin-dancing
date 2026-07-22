"""Tests for scraper health / silent-failure detection.

A scraper writing [] is ambiguous — no Latin events (fine) vs. parser matched
nothing because the page changed (broken). record_scrape_health() tells them
apart via raw_found (events parsed before the keyword filter).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scraper_utils as su


@pytest.fixture
def health_file(tmp_path, monkeypatch):
    path = tmp_path / "scraper-health.json"
    monkeypatch.setattr(su, "SCRAPER_HEALTH_PATH", path)
    return path


def test_structure_found_is_ok_even_with_zero_kept(health_file):
    # Found the page structure (raw_found > 0), just no Latin events this week.
    status = su.record_scrape_health("town-arts", raw_found=29, kept=0)
    assert status == "ok"
    assert su.load_scrape_health()["town-arts"]["status"] == "ok"


def test_zero_raw_on_reachable_page_flags_redesign(health_file):
    status = su.record_scrape_health("somerville-arts", raw_found=0, kept=0)
    assert status == "structure_missing"
    entry = su.load_scrape_health()["somerville-arts"]
    assert entry["status"] == "structure_missing"
    assert entry["note"]  # a human-readable reason is recorded


def test_fetch_failure_is_distinct_from_structure_change(health_file):
    status = su.record_scrape_health("town-arts", raw_found=0, kept=0,
                                     fetched=False, note="fetch failed: timeout")
    assert status == "fetch_error"


def test_health_records_are_per_source_and_persist(health_file):
    su.record_scrape_health("town-arts", raw_found=29, kept=1)
    su.record_scrape_health("somerville-arts", raw_found=0, kept=0)
    health = su.load_scrape_health()
    assert set(health) == {"town-arts", "somerville-arts"}
    assert health["town-arts"]["kept"] == 1
    assert health["somerville-arts"]["status"] == "structure_missing"
