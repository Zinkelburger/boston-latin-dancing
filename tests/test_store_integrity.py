"""Integrity of the event store: strict reads, atomic writes, one lock, and
moves that never lose an event.

Three writers share these files (the MCP server, the cron pipeline, the
review CLIs). Every finding below was a way one of them could silently erase
another's work or a real event:

  * a truncated store read as [] and was written back over the data
  * a shared ".tmp" path let two writers rename each other's half-file
  * a process-level known-duplicates cache wrote stale verdicts back
  * approvals popped the queue row *before* landing the event, so a
    rejection from add_event lost the only copy
  * publish() printed to stdout, which is the MCP server's JSON-RPC channel
  * the tripwire restored files after the slug registry had already retired
    URLs for the publish it was rolling back

Same tmp-dir isolation pattern as test_quarantine.py, extended to venues,
sources, the published file and the lock sidecar.
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import atomic_io
import event_store as es
import scraper_utils
import slug_registry as sr

NY = ZoneInfo("America/New_York")


@pytest.fixture
def store(tmp_path, monkeypatch):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (tmp_path / "public").mkdir()
    monkeypatch.setattr(es, "ROOT", tmp_path)
    monkeypatch.setattr(es, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(es, "ACTIVE_JSON", events_dir / "active.json")
    monkeypatch.setattr(es, "ARCHIVE_JSON", events_dir / "archive.json")
    monkeypatch.setattr(es, "PENDING_JSON", events_dir / "pending.json")
    monkeypatch.setattr(es, "REJECTED_JSON", events_dir / "rejected.json")
    monkeypatch.setattr(es, "BLOCKED_JSON", events_dir / "blocked.json")
    monkeypatch.setattr(es, "CHANGELOG", events_dir / "changelog.jsonl")
    monkeypatch.setattr(es, "SCRAPED_DIR", tmp_path / "scraped")
    monkeypatch.setattr(es, "KNOWN_DUPLICATES_JSON", tmp_path / "known_duplicates.json")
    monkeypatch.setattr(es, "VENUES_JSON", tmp_path / "venues.json")
    monkeypatch.setattr(es, "SOURCES_JSON", tmp_path / "sources.json")
    # The published file must be the one the slug registry reads back
    # (conftest points sr.PUBLISHED at tmp_path / "events-published.json").
    monkeypatch.setattr(es, "PUBLIC_EVENTS_JSON", tmp_path / "events-published.json")
    atomic_io.write_json(tmp_path / "venues.json", [])
    monkeypatch.setattr(es, "_load_source_names", lambda: {})
    monkeypatch.setattr(es, "noisy_source_ids", lambda: set())
    monkeypatch.setattr(es, "unreliable_source_ids", lambda: set())
    monkeypatch.setattr(es, "_trusted_latin_sources", lambda: set())
    return es


def _at(days_from_now: int, hour: int = 20) -> datetime:
    return (datetime.now(NY) + timedelta(days=days_from_now)).replace(
        hour=hour, minute=0, second=0, microsecond=0)


def _event(**overrides):
    start = _at(14)
    base = {
        "id": "evt-1",
        "name": "Salsa Social at the Docks",
        "startDate": start.isoformat(),
        "endDate": (start + timedelta(hours=3)).isoformat(),
        "location": "1 Pier Rd, Boston, MA",
        "lat": 42.36,
        "lng": -71.05,
        "description": "Outdoor salsa social",
        "url": "https://example.com/salsa-docks",
        "styles": ["salsa"],
        "cost": "$10",
        "source": "manual",
    }
    base.update(overrides)
    return base


def _other(**overrides):
    """A second, unrelated event: another night, another name, another venue."""
    start = _at(21)
    base = _event(id="evt-2", name="Bachata Night at the Loft", startDate=start.isoformat(),
                  endDate=(start + timedelta(hours=3)).isoformat(),
                  location="50 Loft St, Somerville, MA", lat=42.39, lng=-71.10,
                  styles=["bachata"], url="https://example.com/loft")
    base.update(overrides)
    return base


def _lock_depth() -> int:
    key = str(es.STORE_LOCK.with_name(es.STORE_LOCK.name + ".lock"))
    entry = atomic_io._locks.get(key)
    return entry.depth if entry else 0


# ── 1. strict reads ───────────────────────────────────────────────────

def test_corrupt_store_raises_instead_of_reading_as_empty(store):
    store.ACTIVE_JSON.write_text('[{"id": "evt-1", "name": "half a rec')
    with pytest.raises(atomic_io.CorruptJSONError):
        store.load_active()


def test_empty_store_file_is_corrupt_not_empty(store):
    store.ARCHIVE_JSON.write_text("")
    with pytest.raises(atomic_io.CorruptJSONError):
        store.load_archive()


def test_missing_store_file_is_empty(store):
    assert store.load_active() == []
    assert store.load_pending() == []
    assert store.load_blocked() == []


def test_add_event_never_overwrites_a_corrupt_store(store):
    broken = '[{"id": "evt-old", "name": "Real event that must survive"'
    store.ACTIVE_JSON.write_text(broken)
    with pytest.raises(atomic_io.CorruptJSONError):
        store.add_event(_event())
    assert store.ACTIVE_JSON.read_text() == broken


def test_corrupt_known_duplicates_raises(store):
    store.KNOWN_DUPLICATES_JSON.write_text("{not json")
    with pytest.raises(atomic_io.CorruptJSONError):
        store._known_duplicate_verdict({"id": "a"}, {"id": "b"})


# ── 2. atomic writes ──────────────────────────────────────────────────

def test_save_is_atomic_and_leaves_no_temp_file(store):
    store.save_active([_event()])
    leftovers = [p for p in store.EVENTS_DIR.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    text = store.ACTIVE_JSON.read_text()
    assert text.endswith("\n")
    assert json.loads(text)[0]["id"] == "evt-1"


def test_changelog_is_appended_one_line_per_entry(store):
    store._append_changelog("add", "a")
    store._append_changelog("add", "b")
    lines = store.CHANGELOG.read_text().splitlines()
    assert [json.loads(l)["event_id"] for l in lines] == ["a", "b"]


def test_known_duplicates_written_atomically(store):
    store._persist_known_duplicate("a", "b", "same")
    assert not list(store.KNOWN_DUPLICATES_JSON.parent.glob("*.tmp"))
    assert store.KNOWN_DUPLICATES_JSON.read_text().endswith("\n")


# ── 3. one store-wide lock ────────────────────────────────────────────

@pytest.fixture
def lock_spy(store, monkeypatch):
    """Record the lock depth at every save_* call."""
    seen: dict[str, list[int]] = {}
    for name in ("save_active", "save_archive", "save_pending", "save_rejected", "save_blocked"):
        real = getattr(store, name)

        def wrapped(data, _real=real, _name=name):
            seen.setdefault(_name, []).append(_lock_depth())
            return _real(data)

        monkeypatch.setattr(store, name, wrapped)
    real_write = atomic_io.write_json

    def spy_write(path, data, **kw):
        seen.setdefault(f"write:{Path(path).name}", []).append(_lock_depth())
        return real_write(path, data, **kw)

    monkeypatch.setattr(atomic_io, "write_json", spy_write)
    return seen


def _assert_all_locked(seen):
    assert seen, "nothing was written"
    for name, depths in seen.items():
        assert all(d >= 1 for d in depths), f"{name} wrote outside the store lock: {depths}"


def test_lifecycle_functions_write_under_the_store_lock(store, lock_spy):
    store.add_event(_event())
    store.add_event(_other())
    store.edit_event("evt-1", {"cost": "$12"})
    assert store.archive_event("evt-2", "done")["status"] == "archived"
    store.remove_active_event("evt-1", reason="oops")
    store.approve_rejected("evt-1")
    store.block_event("evt-1", "other", "no")
    store.unblock_event("evt-1")
    store._persist_known_duplicate("x", "y", "different")
    store.forget_known_duplicate("x", "y")
    _assert_all_locked(lock_spy)
    assert _lock_depth() == 0, "lock leaked after the calls returned"


def test_pending_queue_functions_write_under_the_store_lock(store, lock_spy):
    store.add_event(_event(), quarantine_new=True)
    store.add_event(_other(), quarantine_new=True)
    store.approve_pending("evt-1")
    store.reject_pending("evt-2", "no")
    store.archive_past_events()
    _assert_all_locked(lock_spy)


def test_publish_and_venue_writes_run_under_the_store_lock(store, lock_spy):
    store.add_event(_event())
    store.publish()
    store.add_venue({"name": "Test Hall", "location": "1 Pier Rd, Boston, MA",
                     "url": "https://example.com", "lat": 42.36, "lng": -71.05,
                     "schedule": [{"dayOfWeek": "Friday", "time": "21:00"}]})
    store.add_source({"id": "src-x", "type": "web", "scraper": "generic",
                      "name": "X", "url": "https://example.com/x"})
    _assert_all_locked(lock_spy)


def test_store_lock_is_reentrant_and_released(store):
    with store.store_lock():
        with store.store_lock():
            assert _lock_depth() == 2
            store.add_event(_event())
        assert _lock_depth() == 1
    assert _lock_depth() == 0


def test_store_lock_uses_one_sidecar_for_every_file(store):
    with store.store_lock():
        pass
    assert store.STORE_LOCK.with_name("store.lock").exists()


# ── 4. no known-duplicates cache ──────────────────────────────────────

def test_verdicts_written_by_another_process_are_not_erased(store):
    store._persist_known_duplicate("a", "b", "same")
    # Another process (the pipeline) records its own verdict meanwhile.
    other = atomic_io.read_json(store.KNOWN_DUPLICATES_JSON)
    other.append({"id_a": "c", "id_b": "d", "verdict": "different", "reviewed_at": "x"})
    atomic_io.write_json(store.KNOWN_DUPLICATES_JSON, other)

    store._persist_known_duplicate("e", "f", "same")
    pairs = {(p["id_a"], p["id_b"]) for p in store.list_known_duplicates()}
    assert pairs == {("a", "b"), ("c", "d"), ("e", "f")}
    assert store._known_duplicate_verdict({"id": "d"}, {"id": "c"}) == "skip"
    assert not hasattr(store, "_known_duplicates_cache")


# ── 5. moves never lose an event ──────────────────────────────────────

def test_approve_pending_keeps_the_row_when_landing_fails(store):
    store.add_event(_event(), quarantine_new=True)
    pending = store.load_pending()
    pending[0]["startDate"] = ""          # add_event will reject this
    store.save_pending(pending)

    result = store.approve_pending("evt-1")
    assert result["status"] == "not_approved"
    assert result["add_status"] == "rejected"
    assert [p["id"] for p in store.load_pending()] == ["evt-1"]
    assert store.load_active() == []


def test_approve_pending_rolls_back_a_fresh_verdict_when_landing_fails(store):
    store.add_event(_event())
    # Same name, same night, different venue: a review-tier pair.
    store.add_event(_event(id="evt-2", location="99 Other St, Cambridge, MA",
                           lat=42.40, lng=-71.12, url="https://example.com/other"))
    assert [p["id"] for p in store.load_pending()] == ["evt-2"]
    pending = store.load_pending()
    pending[0]["startDate"] = ""
    store.save_pending(pending)

    result = store.approve_pending("evt-2")
    assert result["status"] == "not_approved"
    assert store._known_duplicate_verdict({"id": "evt-1"}, {"id": "evt-2"}) is None
    assert len(store.load_pending()) == 1


def test_approve_pending_lands_then_removes(store):
    store.add_event(_event(), quarantine_new=True)
    result = store.approve_pending("evt-1")
    assert result["status"] == "added"
    assert store.load_pending() == []
    assert [e["id"] for e in store.load_active()] == ["evt-1"]


def test_approve_rejected_keeps_the_row_when_landing_fails(store):
    store.add_event(_event(name="Community Festival", description="a parade",
                           styles=["other"]))
    rejected = store.load_rejected()
    assert [r["id"] for r in rejected] == ["evt-1"]
    rejected[0]["startDate"] = ""
    store.save_rejected(rejected)

    result = store.approve_rejected("evt-1")
    assert result["status"] == "not_approved"
    assert [r["id"] for r in store.load_rejected()] == ["evt-1"]
    assert store.load_active() == []


def test_remove_active_with_invalid_block_category_changes_nothing(store):
    store.add_event(_event())
    result = store.remove_active_event("evt-1", block=True, block_category="nonsense")
    assert result["status"] == "error"
    assert [e["id"] for e in store.load_active()] == ["evt-1"]
    assert store.load_blocked() == []


def test_remove_active_lands_in_rejected_before_leaving_active(store, monkeypatch):
    store.add_event(_event())
    order: list[str] = []
    real_rej, real_act = store.save_rejected, store.save_active
    monkeypatch.setattr(store, "save_rejected", lambda d: (order.append("rejected"), real_rej(d)))
    monkeypatch.setattr(store, "save_active", lambda d: (order.append("active"), real_act(d)))
    store.remove_active_event("evt-1", reason="gone")
    assert order == ["rejected", "active"]


def test_reactivate_writes_active_before_archive(store, monkeypatch):
    past = _at(-30)
    old = _event(startDate=past.isoformat(), endDate=(past + timedelta(hours=3)).isoformat())
    store.save_archive([old])
    order: list[str] = []
    real_arc, real_act = store.save_archive, store.save_active
    monkeypatch.setattr(store, "save_archive", lambda d: (order.append("archive"), real_arc(d)))
    monkeypatch.setattr(store, "save_active", lambda d: (order.append("active"), real_act(d)))
    result = store.add_event(_event())
    assert result["status"] == "reactivated"
    assert order == ["active", "archive"]
    assert store.load_archive() == []
    assert [e["id"] for e in store.load_active()] == ["evt-1"]


# ── 9. nothing on stdout ──────────────────────────────────────────────

def test_publish_writes_nothing_to_stdout(store, capsys):
    store.add_event(_event())
    store.add_event(_event(id="evt-2", name="Bachata at Nowhere", lat=None, lng=None,
                           location="", url="https://example.com/2"))
    store.publish()
    out, err = capsys.readouterr()
    assert out == ""
    assert "no coordinates" in err


def test_event_store_has_no_stdout_print():
    src = (Path(es.__file__)).read_text()
    for m in re.finditer(r"^\s*print\((.*)$", src, flags=re.M):
        assert "file=sys.stderr" in m.group(1), m.group(0)


# ── 10. tripwire trips before anything is written ─────────────────────

def _fill_active(store, n):
    """n distinct live events: unique names, nights and venues, so neither
    dedup nor series collapsing folds any two of them together."""
    events = []
    for i in range(n):
        start = _at(3 + i)
        events.append(_event(
            id=f"evt-{i}", name=f"Fiesta{i} Social",
            startDate=start.isoformat(), endDate=(start + timedelta(hours=3)).isoformat(),
            location=f"{i + 1} Pier Rd, Boston, MA", lat=42.30 + i * 0.01, lng=-71.05,
            url=f"https://example.com/{i}"))
    store.save_active(events)


def test_tripwire_leaves_every_output_untouched(store):
    _fill_active(store, es.TRIPWIRE_MIN_PREVIOUS + 5)
    first = store.publish_guarded()
    assert first["tripped"] is False
    published_before = store.PUBLIC_EVENTS_JSON.read_text()
    registry_before = sr.REGISTRY_PATH.read_text()
    conflicts_before = store.VENUE_CONFLICTS_JSON.read_text()
    legacy_before = (store.ROOT / "public" / "events.json").read_text()

    store.save_active(store.load_active()[:3])
    result = store.publish_guarded()
    assert result["status"] == "tripwire" and result["tripped"] is True
    assert result["published_live_events"] == 3

    assert store.PUBLIC_EVENTS_JSON.read_text() == published_before
    assert sr.REGISTRY_PATH.read_text() == registry_before
    assert store.VENUE_CONFLICTS_JSON.read_text() == conflicts_before
    assert (store.ROOT / "public" / "events.json").read_text() == legacy_before


def test_tripwire_uses_the_caller_snapshot_when_given(store):
    _fill_active(store, 3)
    baseline = json.dumps([{"id": f"b{i}"} for i in range(es.TRIPWIRE_MIN_PREVIOUS)])
    result = store.publish_guarded(previous_snapshot=baseline)
    assert result["tripped"] is True
    assert not store.PUBLIC_EVENTS_JSON.exists()


# ── 11. errors propagate instead of dropping events ───────────────────

def test_malformed_sources_json_aborts_instead_of_untrusting_everyone(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sources.json").write_text("[{bad json")
    monkeypatch.setattr(scraper_utils, "SOURCES_PATH", data_dir / "sources.json")
    with pytest.raises(atomic_io.CorruptJSONError):
        es._trusted_latin_sources()


def test_corrupt_slug_registry_stops_slug_resolution(store):
    sr.REGISTRY_PATH.write_text("{corrupt")
    events = [{"id": "aaaaaaaa-1", "slug": "x-aaaaaaaa"}, {"id": "aaaaaaaa-2", "slug": "x-aaaaaaaa"}]
    with pytest.raises(atomic_io.CorruptJSONError):
        es._resolve_slug_collisions(events)


# ── 12. location aliases live in data ─────────────────────────────────

def test_location_aliases_load_from_data_file():
    assert es.LOCATION_ALIASES_JSON.name == "location-aliases.json"
    raw = atomic_io.read_json(es.LOCATION_ALIASES_JSON)
    assert isinstance(raw, dict) and any(not k.startswith("_") for k in raw)
    assert es._canonical_location("Rumba y Timbal") == "rumba-y-timbal"
    assert es._canonical_location("Somewhere at 7 Temple St, Boston") == "rumba-y-timbal"
    assert es._canonical_location("A place nobody aliased") is None


def test_location_aliases_loader_flattens_and_skips_notes(tmp_path):
    path = tmp_path / "aliases.json"
    atomic_io.write_json(path, {"_notes": ["ignored"], "my-hall": ["My Hall", " the hall "]})
    assert es._load_location_aliases(path) == {"my hall": "my-hall", "the hall": "my-hall"}
    assert es._load_location_aliases(tmp_path / "missing.json") == {}


# ── 13. every-other-week anchor ───────────────────────────────────────

def _fridays(start: datetime, n: int) -> list[datetime]:
    return [start + timedelta(weeks=i) for i in range(n)]


def test_default_phase_is_unchanged_without_anchor():
    ref = es._EVERY_OTHER_DEFAULT_ANCHOR
    on = [d for d in _fridays(ref, 4) if es._matches_schedule_note(d, "Every other Friday", "Friday")]
    assert on == [ref, ref + timedelta(weeks=2)]


def test_anchor_flips_the_phase():
    ref = es._EVERY_OTHER_DEFAULT_ANCHOR
    opposite = (ref + timedelta(weeks=1)).strftime("%Y-%m-%d")
    on = [d for d in _fridays(ref, 4)
          if es._matches_schedule_note(d, "Every other Friday", "Friday", anchor=opposite)]
    assert on == [ref + timedelta(weeks=1), ref + timedelta(weeks=3)]


def test_venue_with_opposite_phase_expands_on_its_own_weeks(store):
    # Next Friday from today, then the one after: two venues, opposite phases.
    today = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    next_friday = today + timedelta(days=(5 - today.isoweekday()) % 7)
    week_after = next_friday + timedelta(weeks=1)
    venue = {"name": "Alt Hall", "location": "1 Pier Rd, Boston, MA", "lat": 42.36,
             "lng": -71.05, "url": "https://example.com", "styles": ["salsa"]}
    atomic_io.write_json(store.VENUES_JSON, [
        dict(venue, id="phase-a", schedule=[{"dayOfWeek": "Friday", "time": "9:00 PM – 1:00 AM",
                                              "note": "Every other Friday",
                                              "anchor": next_friday.strftime("%Y-%m-%d")}]),
        dict(venue, id="phase-b", schedule=[{"dayOfWeek": "Friday", "time": "9:00 PM – 1:00 AM",
                                              "note": "Every other Friday",
                                              "anchor": week_after.strftime("%Y-%m-%d")}]),
    ])
    by_id = {v["id"]: v for v in store.expand_venues(weeks_ahead=6)}
    dates_a = {r[:10] for r in by_id["phase-a"]["recurrences"]}
    dates_b = {r[:10] for r in by_id["phase-b"]["recurrences"]}
    assert next_friday.strftime("%Y-%m-%d") in dates_a
    assert week_after.strftime("%Y-%m-%d") in dates_b
    assert dates_a.isdisjoint(dates_b)


def test_venues_json_anchor_is_validated():
    ok = [{"dayOfWeek": "Friday", "time": "21:00", "note": "Every other Friday", "anchor": "2026-01-02"}]
    assert es.validate_venue_schedule(ok) == []
    bad = es.validate_venue_schedule([{"dayOfWeek": "Friday", "anchor": "2026-01-01"}])
    assert any("Thursday" in p for p in bad)
    assert es.validate_venue_schedule([{"dayOfWeek": "Friday", "anchor": "next week"}])


# ── 14. stale startDate on a live series rolls forward at publish ─────

def _series(**overrides):
    first = _at(-21)
    occurrences = [first + timedelta(weeks=i) for i in range(6)]
    ev = _event(
        id="series-1", name="Rueda in the Pahk",
        startDate=first.isoformat(), endDate=(first + timedelta(hours=2)).isoformat(),
        recurring=True, dayOfWeek=es.DAYS_LIST[first.isoweekday() % 7],
        recurrences=[d.isoformat() for d in occurrences],
    )
    ev.update(overrides)
    return ev


def test_publish_rolls_a_stale_series_forward(store):
    store.save_active([_series()])
    result = store.publish()
    assert result["series_rolled_forward"] == 1

    published = atomic_io.read_json(store.PUBLIC_EVENTS_JSON)
    live = [e for e in published if not e.get("archived")]
    assert len(live) == 1
    rec = live[0]
    today = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    start = datetime.fromisoformat(rec["startDate"])
    assert start >= today
    assert rec["firstStartDate"] == _series()["startDate"]
    end = datetime.fromisoformat(rec["endDate"])
    assert end - start == timedelta(hours=2)
    assert start == min(datetime.fromisoformat(r) for r in rec["recurrences"]
                        if datetime.fromisoformat(r) >= today)
    # The stored record is untouched.
    assert store.load_active()[0]["startDate"] == _series()["startDate"]
    assert "firstStartDate" not in store.load_active()[0]


def test_publish_leaves_a_current_series_alone(store):
    first = _at(3)
    ev = _series(startDate=first.isoformat(), endDate=(first + timedelta(hours=2)).isoformat(),
                 recurrences=[(first + timedelta(weeks=i)).isoformat() for i in range(4)])
    store.save_active([ev])
    result = store.publish()
    assert result["series_rolled_forward"] == 0
    published = atomic_io.read_json(store.PUBLIC_EVENTS_JSON)
    assert "firstStartDate" not in published[0]
    assert published[0]["startDate"] == first.isoformat()


# ── 15. archived rows ship a preview, not the essay ───────────────────

def test_truncate_description_cuts_at_a_word_boundary():
    text = " ".join(f"word{i}" for i in range(120))
    cut = es._truncate_description(text, 300)
    assert len(cut) <= 300
    assert cut.endswith("…")
    assert not cut[:-1].endswith("word") or cut[:-1].split()[-1].startswith("word")
    assert cut[:-1] == text[:len(cut) - 1]           # a prefix, not a rewrite
    assert text[len(cut) - 1] == " "                 # cut on a space
    assert es._truncate_description("short", 300) == "short"


def test_publish_truncates_archived_descriptions_only(store):
    long_text = "Salsa " * 200
    past = _at(-40)
    archived = _event(id="old-1", name="Old Salsa Fest", description=long_text,
                      startDate=past.isoformat(), endDate=(past + timedelta(hours=3)).isoformat())
    store.save_archive([archived])
    store.save_active([_event(description=long_text)])
    store.publish()
    published = atomic_io.read_json(store.PUBLIC_EVENTS_JSON)
    by_id = {e["id"]: e for e in published}
    assert by_id["old-1"]["archived"] is True
    assert len(by_id["old-1"]["description"]) <= es.ARCHIVED_DESCRIPTION_LIMIT
    assert by_id["old-1"]["description"].endswith("…")
    assert by_id["evt-1"]["description"] == long_text


# ── 17. add_event loads each store once ───────────────────────────────

def test_add_event_reads_each_store_at_most_once(store, monkeypatch):
    store.save_archive([])
    counts: dict[str, int] = {}
    for name in ("load_active", "load_archive", "load_blocked", "load_pending", "load_rejected"):
        real = getattr(store, name)

        def counted(_real=real, _name=name):
            counts[_name] = counts.get(_name, 0) + 1
            return _real()

        monkeypatch.setattr(store, name, counted)
    assert store.add_event(_event())["status"] == "added"
    assert all(n <= 1 for n in counts.values()), counts
    assert counts["load_active"] == 1 and counts["load_archive"] == 1


# ── 18. new public functions for the MCP server ───────────────────────

def test_archive_event_moves_one_event_and_logs(store):
    store.add_event(_event())
    store.add_event(_other())
    result = store.archive_event("evt-1", "venue closed")
    assert result["status"] == "archived"
    assert result["event"]["id"] == "evt-1" and result["event"]["archivedAt"]
    assert [e["id"] for e in store.load_active()] == ["evt-2"]
    assert [e["id"] for e in store.load_archive()] == ["evt-1"]
    log = [json.loads(l) for l in store.CHANGELOG.read_text().splitlines()]
    assert {"action": "archive", "event_id": "evt-1", "details": "venue closed"}.items() <= log[-1].items()
    assert store.archive_event("nope")["status"] == "not_found"


def test_validate_venue_schedule_reports_every_problem():
    assert es.validate_venue_schedule([]) != []
    assert es.validate_venue_schedule("Friday") != []
    problems = es.validate_venue_schedule([
        {"dayOfWeek": "Funday", "time": "late", "note": 3},
        "not a dict",
    ])
    assert len(problems) == 4
    assert es.validate_venue_schedule([
        {"dayOfWeek": "Saturday", "time": "9:00 PM – 2:00 AM", "note": "70% Bachata"},
        {"dayOfWeek": "Sunday", "time": "19:00"},
    ]) == []


def test_add_venue_validates_dedups_and_appends_atomically(store):
    venue = {"name": "Test Hall", "location": "1 Pier Rd, Boston, MA", "url": "https://example.com",
             "lat": 42.36, "lng": -71.05, "styles": ["salsa"],
             "schedule": [{"dayOfWeek": "Friday", "time": "9:00 PM – 1:00 AM"}]}
    bad = store.add_venue({"name": "", "schedule": []})
    assert bad["status"] == "invalid" and len(bad["problems"]) >= 3

    ok = store.add_venue(venue)
    assert ok["status"] == "added" and ok["problems"] == []
    assert ok["venue"]["id"] == "test-hall"
    assert atomic_io.read_json(store.VENUES_JSON)[0]["name"] == "Test Hall"
    assert not list(store.VENUES_JSON.parent.glob(".venues.json.*.tmp"))

    again = store.add_venue(dict(venue, name="test hall"))
    assert again["status"] == "exists"
    assert len(atomic_io.read_json(store.VENUES_JSON)) == 1


def test_add_source_validates_and_rejects_duplicate_ids(store):
    assert store.add_source({"id": "x"})["status"] == "invalid"
    missing = store.add_source({"id": "x", "type": "web", "scraper": "generic", "name": "X"})
    assert missing["status"] == "invalid" and any("url" in p for p in missing["problems"])

    src = {"id": "x", "type": "web", "scraper": "generic", "name": "X", "url": "https://example.com"}
    assert store.add_source(src)["status"] == "added"
    stored = atomic_io.read_json(store.SOURCES_JSON)
    assert stored[0]["enabled"] is True
    assert store.add_source(src)["status"] == "exists"
    assert len(atomic_io.read_json(store.SOURCES_JSON)) == 1
    queries = {"id": "y", "type": "search", "scraper": "generic", "name": "Y", "search_queries": ["salsa"]}
    assert store.add_source(queries)["status"] == "added"


# ── 19. non-Latin events queue for review rather than vanish ──────────

def test_non_latin_event_lands_in_rejected_queue(store):
    result = store.add_event(_event(name="Community Festival", description="a parade",
                                    styles=["other"]))
    assert result["status"] == "rejected_non_latin"
    assert [r["id"] for r in store.load_rejected()] == ["evt-1"]
    assert store.load_active() == []
