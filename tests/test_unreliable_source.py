"""Sources marked unreliable scrape for research but never enter the map."""

import json
from pathlib import Path

import event_store as es
import source_signal as ss


def test_unabulla_is_unreliable():
    assert "unabulla-cuban-boston" in ss.unreliable_source_ids()


def test_ingest_skips_unreliable_source(tmp_path, monkeypatch):
    scraped = tmp_path / "scraped"
    scraped.mkdir()
    (scraped / "unabulla-cuban-boston.json").write_text(json.dumps([
        {
            "id": "fake-unabulla-1",
            "name": "Fake Una Bulla Party",
            "startDate": "2026-09-15T20:30:00-04:00",
            "endDate": "2026-09-15T23:00:00-04:00",
            "location": "Rumba Y Timbal Dance Company, 7 Temple St, Cambridge, MA",
            "styles": ["salsa"],
            "source": "unabulla-cuban-boston",
        }
    ]))
    monkeypatch.setattr(es, "SCRAPED_DIR", scraped)
    monkeypatch.setattr(es, "load_active", lambda: [])
    monkeypatch.setattr(es, "save_active", lambda events: None)
    monkeypatch.setattr(es, "load_archive", lambda: [])
    monkeypatch.setattr(es, "load_pending", lambda: [])
    monkeypatch.setattr(es, "save_pending", lambda events: None)
    monkeypatch.setattr(es, "load_blocked", lambda: [])
    monkeypatch.setattr(es, "_append_changelog", lambda *a, **k: None)

    result = es.ingest_scraped("unabulla-cuban-boston")
    assert result["skipped_unreliable"] == 1
    assert result["added"] == 0
