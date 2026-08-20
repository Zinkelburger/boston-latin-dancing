"""Shared test isolation.

Several writers use module-level paths that the per-test fixtures don't
override, so without this a plain `pytest` run rewrites tracked files in
data/:

* _log_dedup() appends to DEDUP_LOG (data/events/dedup-log.jsonl).
* publish() writes VENUE_CONFLICTS_JSON and calls slug_registry.update(),
  which reads the real data/events-published.json and rewrites the real
  data/slug-registry.json.

Redirect all of them to tmp files for every test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es
import slug_registry as sr


@pytest.fixture(autouse=True)
def _isolate_dedup_log(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "DEDUP_LOG", tmp_path / "dedup-log.jsonl")
    monkeypatch.setattr(es, "VENUE_CONFLICTS_JSON", tmp_path / "venue-conflicts.json")
    monkeypatch.setattr(sr, "REGISTRY_PATH", tmp_path / "slug-registry.json")
    monkeypatch.setattr(sr, "PUBLISHED", tmp_path / "events-published.json")
