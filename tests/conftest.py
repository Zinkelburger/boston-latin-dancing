"""Shared test isolation.

Every module-level path in the event store, the slug registry and the
scraper utilities is redirected into the test's tmp_path for every test,
so a plain ``pytest`` run can never rewrite tracked files under data/ and
no test can leak state into another. Tests that exercise the on-disk
lifecycle request the ``store`` fixture, which returns the already
isolated ``event_store`` module; a module may override ``store`` and
request the shared one to layer extra setup on top.

data/venues.json and data/sources.json are deliberately left real: a
handful of tests assert facts about the checked-in configuration. Tests
that write venues or sources patch ``VENUES_JSON`` / ``SOURCES_JSON`` /
``scraper_utils.SOURCES_PATH`` themselves.

Nothing here may reach the network. ``geocode`` is stubbed to a miss and
``requests.get`` / ``requests.post`` raise; a test that needs a canned
response monkeypatches them at test level, which takes precedence.
"""

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es
import scraper_utils as su
import slug_registry as sr


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    scraped_dir = tmp_path / "scraped"
    scraped_dir.mkdir()
    (tmp_path / "public").mkdir()

    monkeypatch.setattr(es, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(es, "STORE_LOCK", events_dir / "store")
    monkeypatch.setattr(es, "ACTIVE_JSON", events_dir / "active.json")
    monkeypatch.setattr(es, "ARCHIVE_JSON", events_dir / "archive.json")
    monkeypatch.setattr(es, "PENDING_JSON", events_dir / "pending.json")
    monkeypatch.setattr(es, "REJECTED_JSON", events_dir / "rejected.json")
    monkeypatch.setattr(es, "BLOCKED_JSON", events_dir / "blocked.json")
    monkeypatch.setattr(es, "CHANGELOG", events_dir / "changelog.jsonl")
    monkeypatch.setattr(es, "DEDUP_LOG", events_dir / "dedup-log.jsonl")
    monkeypatch.setattr(es, "VENUE_CONFLICTS_JSON", events_dir / "venue-conflicts.json")
    monkeypatch.setattr(es, "SCRAPED_DIR", scraped_dir)
    monkeypatch.setattr(es, "KNOWN_DUPLICATES_JSON", tmp_path / "known_duplicates.json")
    monkeypatch.setattr(es, "PUBLIC_EVENTS_JSON", tmp_path / "events-published.json")
    # publish() resolves the legacy public/events.json copy from ROOT at call time.
    monkeypatch.setattr(es, "ROOT", tmp_path)

    monkeypatch.setattr(sr, "REGISTRY_PATH", tmp_path / "slug-registry.json")
    monkeypatch.setattr(sr, "PUBLISHED", tmp_path / "events-published.json")

    monkeypatch.setattr(su, "SCRAPED_DIR", scraped_dir)
    monkeypatch.setattr(su, "SCRAPER_HEALTH_PATH", tmp_path / "scraper-health.json")
    monkeypatch.setattr(su, "GEOCODE_CACHE_PATH", tmp_path / "geocode-cache.json")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*args, **kwargs):
        raise AssertionError(f"test tried to reach the network: {args[:1]}")

    monkeypatch.setattr(requests, "get", _refuse)
    monkeypatch.setattr(requests, "post", _refuse)
    monkeypatch.setattr(su, "geocode", lambda location: None)


@pytest.fixture
def store():
    """The event_store module with every path already pointed at tmp_path."""
    return es
