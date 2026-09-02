"""MCP server: every tool answers with JSON (errors included), dry_run
never writes, and scraping goes through the shared pipeline runner.

Store paths are redirected to tmp_path (same pattern as
test_block_lifecycle.py). The event_store entry points that other parts of
the pipeline provide (archive_event, add_venue, add_source,
validate_venue_schedule) and the scraper runner are stubbed here, so this
file exercises the server's own behaviour only.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import event_store as es  # noqa: E402
import run_pipeline  # noqa: E402
import scraper_utils  # noqa: E402

# The server binds these names at import time. Guarantee they exist (a real
# implementation is used when present) so the module imports standalone.
for _name in ("archive_event", "add_venue", "add_source", "validate_venue_schedule"):
    if not hasattr(es, _name):
        setattr(es, _name, lambda *a, **k: pytest.fail(f"unstubbed event_store.{_name} called"))
if not hasattr(run_pipeline, "run_scrapers"):
    run_pipeline.run_scrapers = lambda *a, **k: pytest.fail("unstubbed run_scrapers called")



def _load_mcp_server():
    """Import mcp-server/server.py under its own module name.

    backend/server.py is also imported as ``server`` by the API tests; a plain
    ``import server`` here would return whichever loaded first.
    """
    spec = importlib.util.spec_from_file_location("bld_mcp_server", ROOT / "mcp-server" / "server.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bld_mcp_server"] = module
    spec.loader.exec_module(module)
    return module


srv = _load_mcp_server()


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
    monkeypatch.setattr(es, "VENUES_JSON", tmp_path / "venues.json")
    monkeypatch.setattr(es, "KNOWN_DUPLICATES_JSON", tmp_path / "known_duplicates.json")
    monkeypatch.setattr(srv, "VENUES_JSON", tmp_path / "venues.json")
    # Never geocode over the network from a test.
    monkeypatch.setattr(scraper_utils, "geocode", lambda location: (42.36, -71.06))
    monkeypatch.setattr(srv, "geocode", lambda location: (42.36, -71.06))
    return es


def _event(**overrides):
    base = {
        "id": "evt-1",
        "name": "Salsa Social",
        "startDate": "2099-07-01T20:00:00-04:00",
        "endDate": "2099-07-01T23:00:00-04:00",
        "location": "Boston, MA",
        "lat": 42.36,
        "lng": -71.06,
        "styles": ["salsa"],
        "source": "test-source",
    }
    base.update(overrides)
    return base


def _call(fn, **kwargs):
    raw = fn(**kwargs)
    assert isinstance(raw, str), f"{fn.__name__} returned {type(raw).__name__}, not a JSON string"
    return json.loads(raw)


# ── Registration ──────────────────────────────────────────────────────


def _tool_schemas():
    tools = asyncio.run(srv.mcp.list_tools())
    return {t.name: (getattr(t, "input_schema", None) or getattr(t, "inputSchema")) for t in tools}


def test_wrapper_preserves_tool_signatures():
    schemas = _tool_schemas()
    assert "event_add" in schemas
    assert "dry_run" in schemas["event_add"]["properties"]
    assert "limit" in schemas["event_list"]["properties"]
    for name in ("event_remove", "event_block", "known_duplicate_forget", "event_approve"):
        assert "dry_run" in schemas[name]["properties"], name


def test_every_tool_is_wrapped():
    for name in _tool_schemas():
        assert getattr(srv, name).__wrapped__ is not None, name


# ── Error wrapper ─────────────────────────────────────────────────────


def test_corrupt_json_error_passes_through_verbatim(store, monkeypatch, capsys):
    bad = store.ACTIVE_JSON
    exc = srv.CorruptJSONError(bad, ValueError("Expecting value: line 1 column 1"))

    def boom():
        raise exc

    monkeypatch.setattr(srv, "load_active", boom)
    result = _call(srv.event_get, event_id="anything")
    assert result["type"] == "CorruptJSONError"
    assert result["error"] == str(exc)
    assert str(bad) in result["error"]
    assert result["path"] == str(bad)
    out, err = capsys.readouterr()
    assert out == ""  # stdout is the MCP transport
    assert str(bad) in err


def test_key_error_becomes_payload(store, monkeypatch, capsys):
    monkeypatch.setattr(srv, "load_venue_conflicts",
                        lambda: {"conflicts": [], "suppressed": [{"id": "x"}]})
    result = _call(srv.event_list, status="venue_conflict")
    assert result["type"] == "KeyError"
    assert "event" in result["error"]
    assert capsys.readouterr().out == ""


def test_value_error_becomes_payload(store, monkeypatch):
    def boom():
        raise ValueError("live count collapsed")

    monkeypatch.setattr(srv, "publish_guarded", boom)
    assert _call(srv.event_publish) == {"error": "live count collapsed", "type": "ValueError"}


def test_invalid_json_arguments_are_errors_not_exceptions(store):
    assert _call(srv.event_edit, event_id="evt-1", updates_json="{not json")["type"] == "JSONDecodeError"
    assert _call(srv.venue_add, venue_id="v", name="V", location="L", schedule_json="[")["type"] == "JSONDecodeError"
    assert _call(srv.event_add, name="X", start_date="tomorrow", location="L")["type"] == "ValueError"


def test_event_list_rejects_negative_limit(store):
    result = _call(srv.event_list, limit=-1)
    assert result["type"] == "ValueError"
    assert "limit" in result["error"]


# ── dry_run ───────────────────────────────────────────────────────────


def test_event_add_dry_run_reports_without_writing(store):
    assert _call(srv.event_add, name="Salsa Social", start_date="2099-07-01T20:00:00-04:00",
                 location="Boston, MA", styles="salsa", dry_run=True)["would"].startswith("add to active")
    assert store.load_active() == []

    store.save_active([_event()])
    report = _call(srv.event_add, name="Salsa Social", start_date="2099-07-01T20:00:00-04:00",
                   location="Boston, MA", styles="salsa", event_id="evt-1", force=True, dry_run=True)
    assert report["dry_run"] is True
    assert report["duplicate"]["id"] == "evt-1"
    assert "merge into active event evt-1" in report["would"]
    assert [e["id"] for e in store.load_active()] == ["evt-1"]
    # The event the tool would write has the scraper builder's shape.
    assert report["event"]["dayOfWeek"] == "Wednesday"
    assert report["event"]["styles"] == ["salsa"]


def test_event_add_uses_make_event(store, monkeypatch):
    seen = {}
    real = srv.make_event

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(srv, "make_event", spy)
    result = _call(srv.event_add, name="Bachata Night", start_date="2099-07-02T21:00:00-04:00",
                   location="Boston, MA", styles="bachata,salsa", source="manual")
    assert result["status"] == "added"
    assert seen["styles"] == ["bachata", "salsa"]
    assert seen["source"] == "manual"
    added = store.load_active()[0]
    assert added["id"].startswith("manual-")
    assert added["dayOfWeek"] == "Thursday"


def test_event_remove_dry_run_leaves_active_alone(store):
    store.save_active([_event()])
    report = _call(srv.event_remove, event_id="evt-1", block=True, block_category="defunct", dry_run=True)
    assert report == {
        "dry_run": True, "event_id": "evt-1", "name": "Salsa Social",
        "would": "remove from active and block permanently (defunct)",
        "reason": "removed from active",
    }
    assert len(store.load_active()) == 1
    assert store.load_blocked() == []
    assert _call(srv.event_remove, event_id="nope", dry_run=True)["type"] == "NotFound"


def test_event_remove_rejects_bad_category(store):
    assert _call(srv.event_remove, event_id="evt-1", block=True, block_category="meh")["type"] == "ValueError"


def test_event_block_dry_run(store):
    store.save_rejected([_event(id="rej-1", name="Drum Circle")])
    report = _call(srv.event_block, event_id="rej-1", category="not_dance", dry_run=True)
    assert report["found_in"] == "rejected"
    assert "block permanently (not_dance)" in report["would"]
    assert store.load_blocked() == []
    assert len(store.load_rejected()) == 1
    assert _call(srv.event_block, event_id="ghost", category="other", dry_run=True)["type"] == "NotFound"
    assert _call(srv.event_block, event_id="rej-1", category="bogus")["type"] == "ValueError"


def test_known_duplicate_forget_dry_run(store, monkeypatch):
    verdict = {"id_a": "a", "id_b": "b", "verdict": "same", "reviewed_at": "2026-01-01T00:00:00+00:00"}
    monkeypatch.setattr(srv, "list_known_duplicates", lambda: [verdict])
    deleted = []
    monkeypatch.setattr(srv, "forget_known_duplicate", lambda a, b: deleted.append((a, b)) or {"status": "forgotten"})

    report = _call(srv.known_duplicate_forget, id_a="b", id_b="a", dry_run=True)
    assert report["would"] == "delete this verdict"
    assert report["verdicts"] == [verdict]
    assert deleted == []

    assert "nothing" in _call(srv.known_duplicate_forget, id_a="x", id_b="y", dry_run=True)["would"]
    assert _call(srv.known_duplicate_forget, id_a="a", id_b="b") == {"status": "forgotten"}
    assert deleted == [("a", "b")]


def test_event_approve_dry_run(store, monkeypatch):
    store.save_active([_event(id="series-1", name="Salsa Social", recurring=True)])
    store.save_pending([_event(id="pend-1", name="Salsa Social 10th Anniversary",
                               _dedup_candidate_of="series-1", _dedup_reason="fuzzy name")])
    approved = []
    monkeypatch.setattr(srv, "approve_pending", lambda eid, force=False: approved.append(eid) or {})

    report = _call(srv.event_approve, event_id="pend-1", dry_run=True)
    assert report["merge_into"] == "series-1"
    assert report["special_edition_mismatch"] is True
    assert "refuse" in report["would"]
    assert "force=True" in report["would"]
    assert approved == []
    assert len(store.load_pending()) == 1

    forced = _call(srv.event_approve, event_id="pend-1", force=True, dry_run=True)
    assert forced["would"].startswith("merge into series-1")
    assert _call(srv.event_approve, event_id="ghost", dry_run=True)["type"] == "NotFound"


# ── Delegation to the shared store / runner ───────────────────────────


def test_event_scrape_delegates_to_run_scrapers(store, monkeypatch):
    calls = {}

    def fake_run_scrapers(only=None, timeout=180):
        calls["only"] = only
        calls["timeout"] = timeout
        return [
            {"source_id": "lous-live", "ok": True, "returncode": 0, "seconds": 1.2, "stderr_tail": ""},
            {"source_id": "jandl-events", "ok": False, "returncode": 1, "seconds": 0.4, "stderr_tail": "boom"},
        ]

    monkeypatch.setattr(srv, "run_scrapers", fake_run_scrapers)
    monkeypatch.setattr(srv, "ingest_scraped", lambda sid, quarantine_new=False: {"sid": sid, "q": quarantine_new})
    monkeypatch.setattr(srv, "archive_past_events", lambda: [])

    result = _call(srv.event_scrape, quarantine_new=True)
    assert calls == {"only": None, "timeout": srv.SCRAPE_TIMEOUT_SECONDS}
    assert result["scrapers_failed"] == ["jandl-events"]
    assert result["scrape_results"][1]["stderr_tail"] == "boom"
    assert result["ingestion"] == {"sid": None, "q": True}

    _call(srv.event_scrape, source_id="lous-live")
    assert calls["only"] == "lous-live"


def test_event_scrape_unknown_source(store, monkeypatch):
    monkeypatch.setattr(srv, "run_scrapers", lambda only=None, timeout=180: [])
    assert _call(srv.event_scrape, source_id="nope")["type"] == "ValueError"


def test_event_archive_single_uses_store_entry_point(store, monkeypatch):
    calls = []
    monkeypatch.setattr(srv, "archive_event",
                        lambda eid, reason="": calls.append((eid, reason)) or {"status": "archived", "event_id": eid})
    assert _call(srv.event_archive, event_id="evt-1", reason="ended") == {"status": "archived", "event_id": "evt-1"}
    assert calls == [("evt-1", "ended")]


def test_venue_add_validates_then_delegates(store, monkeypatch):
    monkeypatch.setattr(srv, "validate_venue_schedule", lambda s: ["dayOfWeek missing"] if not s else [])
    added = []
    monkeypatch.setattr(srv, "add_venue", lambda v: added.append(v) or {"status": "added", "id": v["id"]})

    bad = _call(srv.venue_add, venue_id="v", name="V", location="L", schedule_json="[]")
    assert bad["type"] == "ValueError" and bad["issues"] == ["dayOfWeek missing"]
    assert added == []

    ok = _call(srv.venue_add, venue_id="havana", name="Havana Club", location="288 Green St, Cambridge, MA",
               schedule_json='[{"dayOfWeek": "Friday", "time": "9:00 PM", "note": ""}]', styles="salsa")
    assert ok == {"status": "added", "id": "havana"}
    assert added[0]["schedule"][0]["dayOfWeek"] == "Friday"
    assert added[0]["lat"] == 42.36
    assert added[0]["styles"] == ["salsa"]


def test_source_add_rejects_malformed_config(store, monkeypatch):
    added = []
    monkeypatch.setattr(srv, "add_source", lambda s: added.append(s) or {"status": "added"})

    bad = _call(srv.source_add, source_id="s", source_type="ics", name="S", scraper="scrape_ics.py",
                config_json="{oops")
    assert bad["type"] == "JSONDecodeError"
    assert "config_json" in bad["error"]
    assert _call(srv.source_add, source_id="s", source_type="ics", name="S", scraper="scrape_ics.py",
                 config_json="[1]")["type"] == "ValueError"
    assert added == []

    ok = _call(srv.source_add, source_id="s", source_type="ics", name="S", scraper="scrape_ics.py",
               url="https://x", config_json='{"ics_url": "https://x/cal.ics"}')
    assert ok == {"status": "added"}
    assert added[0]["ics_url"] == "https://x/cal.ics"
    assert added[0]["enabled"] is True


def test_venue_list_reports_corrupt_file(store):
    srv.VENUES_JSON.write_text("{not json")
    result = _call(srv.venue_list)
    assert result["type"] == "CorruptJSONError"
    assert str(srv.VENUES_JSON) in result["error"]


def test_no_stdout_prints_in_module():
    src = (ROOT / "mcp-server" / "server.py").read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("print("):
            assert "file=sys.stderr" in stripped, line


def test_location_override_goes_through_edit_event(store, monkeypatch):
    store.save_active([_event(lat=None, lng=None)])
    monkeypatch.setattr(store, "geocode", lambda location: (42.37, -71.10))

    result = _call(srv.event_set_location_override, event_id="evt-1", location="288 Green St, Cambridge, MA")
    assert result == {"status": "override_set", "event_id": "evt-1",
                      "location": "288 Green St, Cambridge, MA", "geocoded": True}
    saved = store.load_active()[0]
    assert saved["_location_override"] == "288 Green St, Cambridge, MA"
    assert (saved["lat"], saved["lng"]) == (42.37, -71.10)
    # The changelog entry comes from the store, not a private helper.
    assert '"edit"' in store.CHANGELOG.read_text()
    assert _call(srv.event_set_location_override, event_id="ghost", location="x")["type"] == "NotFound"
