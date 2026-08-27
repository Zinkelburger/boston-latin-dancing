"""A link the reviewer deletes must stay deleted across re-scrapes.

merge_event() accumulates every URL a source has ever carried into urls[], so
clearing a dead alternate only held until the next ingest handed it back. The
same expired Instagram post and Facebook share wrapper kept resurfacing in
check-links week after week, and each weekly review cleared them again.

Same tmp-dir isolation pattern as test_quarantine.py.
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
    monkeypatch.setattr(es, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(es, "ACTIVE_JSON", events_dir / "active.json")
    monkeypatch.setattr(es, "ARCHIVE_JSON", events_dir / "archive.json")
    monkeypatch.setattr(es, "PENDING_JSON", events_dir / "pending.json")
    monkeypatch.setattr(es, "REJECTED_JSON", events_dir / "rejected.json")
    monkeypatch.setattr(es, "BLOCKED_JSON", events_dir / "blocked.json")
    monkeypatch.setattr(es, "CHANGELOG", events_dir / "changelog.jsonl")
    return es


NY = ZoneInfo("America/New_York")
GOOD = "https://www.wearelous.com/lous-live/salsa-night"
DEAD = "https://www.instagram.com/p/DY5afzhhsK7/"


def _soon() -> str:
    return (datetime.now(NY) + timedelta(days=10)).replace(
        hour=20, minute=0, second=0, microsecond=0
    ).isoformat()


def _event(**over) -> dict:
    ev = {
        "id": "rueda-in-the-pahk",
        "name": "Rueda in the Pahk",
        "startDate": _soon(),
        "endDate": _soon(),
        "location": "Jill Brown Rhone Park, 900 Main St, Cambridge, MA 02139",
        "lat": 42.3653,
        "lng": -71.1034,
        "description": "Free rueda de casino lesson, then social dancing.",
        "url": GOOD,
        "urls": [DEAD],
        "styles": ["salsa"],
        "cost": "Free",
        "source": "beatrice-calendar",
    }
    ev.update(over)
    return ev


def test_cleared_alt_link_survives_a_rescrape(store):
    store.save_active([_event()])
    store.edit_event("rueda-in-the-pahk", {"urls": []})

    store.add_event(_event(), skip_latin_check=True)   # source hands it back

    stored = store.load_active()[0]
    assert DEAD not in store._event_url_list(stored)
    assert stored["url"] == GOOD


def test_removal_is_recorded_but_never_published(store):
    store.save_active([_event()])
    store.edit_event("rueda-in-the-pahk", {"urls": []})

    stored = store.load_active()[0]
    assert stored["_dropped_urls"] == [DEAD]

    store._strip_internal_fields(stored, {})
    assert "_dropped_urls" not in stored


def test_putting_a_link_back_overrides_the_removal(store):
    store.save_active([_event()])
    store.edit_event("rueda-in-the-pahk", {"urls": []})
    store.edit_event("rueda-in-the-pahk", {"urls": [DEAD]})

    store.add_event(_event(), skip_latin_check=True)

    stored = store.load_active()[0]
    assert DEAD in store._event_url_list(stored)
    assert not stored.get("_dropped_urls")


def test_event_is_never_left_with_no_link_at_all(store):
    """Suppression yields to the last link standing — stale beats nothing."""
    store.save_active([_event(url=DEAD, urls=[])])
    store.edit_event("rueda-in-the-pahk", {"url": None})

    store.add_event(_event(url=DEAD, urls=[]), skip_latin_check=True)

    assert store.load_active()[0]["url"] == DEAD


def test_unrelated_edits_do_not_drop_links(store):
    store.save_active([_event()])
    store.edit_event("rueda-in-the-pahk", {"cost": "$10"})

    stored = store.load_active()[0]
    assert not stored.get("_dropped_urls")
    assert DEAD in store._event_url_list(stored)
