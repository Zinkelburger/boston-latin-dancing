"""Shared test isolation.

_log_dedup() appends to a module-level DEDUP_LOG path that the per-test fixtures
don't override, so without this every test run would pollute the real
data/events/dedup-log.jsonl. Redirect it to a tmp file for every test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es


@pytest.fixture(autouse=True)
def _isolate_dedup_log(tmp_path, monkeypatch):
    monkeypatch.setattr(es, "DEDUP_LOG", tmp_path / "dedup-log.jsonl")
