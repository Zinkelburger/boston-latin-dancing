"""Sources marked unreliable scrape for research but never enter the map.

The flag lives in data/sources.json; these tests build their own registry
so they pin the mechanism rather than whichever real source happens to
carry the flag today.
"""

import json

import event_store as es
import scraper_utils as su
import source_signal as ss


def _sources(tmp_path, monkeypatch, entries):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(su, "SOURCES_PATH", path)
    monkeypatch.setattr(es, "SOURCES_JSON", path)
    return path


def test_unreliable_flag_is_read_from_sources_json(tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch, [
        {"id": "flaky-cal", "type": "ics", "scraper": "scrape_ics.py", "name": "Flaky",
         "url": "https://example.com/a.ics", "enabled": True, "unreliable": True},
        {"id": "solid-cal", "type": "ics", "scraper": "scrape_ics.py", "name": "Solid",
         "url": "https://example.com/b.ics", "enabled": True},
    ])
    assert ss.unreliable_source_ids() == {"flaky-cal"}


def test_ingest_skips_unreliable_source(store, tmp_path, monkeypatch):
    _sources(tmp_path, monkeypatch, [
        {"id": "flaky-cal", "type": "ics", "scraper": "scrape_ics.py", "name": "Flaky",
         "url": "https://example.com/a.ics", "enabled": True, "unreliable": True},
    ])
    (store.SCRAPED_DIR / "flaky-cal.json").write_text(json.dumps([
        {
            "id": "fake-flaky-1",
            "name": "Fake Cuban Party",
            "startDate": "2099-09-15T20:30:00-04:00",
            "endDate": "2099-09-15T23:00:00-04:00",
            "location": "7 Temple St, Cambridge, MA",
            "lat": 42.37,
            "lng": -71.10,
            "styles": ["salsa"],
            "source": "flaky-cal",
        }
    ]), encoding="utf-8")

    result = store.ingest_scraped("flaky-cal")

    assert result["skipped_unreliable"] == 1
    assert result["added"] == 0
    assert store.load_active() == []
    assert store.load_pending() == []
