"""The shared scraper runner, registry, HTTP helper, and per-scraper parsers.

Every scraper's main() goes through scraper_utils.run_scraper, and which
scrapers exist is decided by data/sources.json alone. These tests pin the
failure semantics (a failing scraper exits 1, records a health failure, and
keeps its stale file), the registry contract that run_pipeline and the MCP
server code against, and one fixture-driven parse per scraper so a markup
regression shows up here rather than as an empty file in production.

Nothing here touches the network: requests.get is replaced with an
assertion, and geocode() returns None.
"""

from __future__ import annotations

import functools
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scraper_utils as su  # noqa: E402
from atomic_io import read_json, write_json  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 9, 1)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def response(body: str | bytes, status: int = 200, content_type: str = "text/html") -> requests.Response:
    """A real requests.Response so .text/.json()/raise_for_status behave."""
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode("utf-8") if isinstance(body, str) else body
    resp.headers["content-type"] = content_type
    resp.url = "https://example.test/"
    return resp


def future_event(source_id: str, days_ahead: int = 30, **overrides) -> dict:
    start = datetime.now(su.NY_TZ).replace(microsecond=0) + timedelta(days=days_ahead)
    ev = su.make_event(
        id=f"{source_id}-{days_ahead}", name="Salsa Social", start=start,
        location="Havana Club, 288 Green St, Cambridge, MA", source=source_id,
    )
    ev.update(overrides)
    return ev


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network, no geocoding, no real sleeping — in every test here."""
    def _no_network(*args, **kwargs):
        raise AssertionError(f"test tried to reach the network: {args[:1]}")
    monkeypatch.setattr(requests, "get", _no_network)
    monkeypatch.setattr(su, "geocode", lambda loc: None)
    monkeypatch.setattr(su.time, "sleep", lambda s: None)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A temp sources.json plus temp scraped/health paths, wired into scraper_utils."""
    sources = [
        {"id": "beatrice-calendar", "type": "ics", "scraper": "scrape_ics.py",
         "name": "Beatrice", "url": "https://example.test/beatrice.ics", "enabled": True},
        {"id": "timba-messengers", "type": "ics", "scraper": "scrape_ics.py",
         "name": "Timba", "url": "https://example.test/timba.ics", "enabled": True},
        {"id": "retired-source", "type": "ics", "scraper": "scrape_ics.py",
         "name": "Retired", "url": "https://example.test/old.ics", "enabled": False},
        {"id": "manual-only", "type": "manual", "name": "No scraper", "enabled": True},
        {"id": "bobas", "type": "facebook", "scraper": "scrape_facebook.py",
         "name": "BOBAS", "facebook_events_url": "https://www.facebook.com/bobas/events",
         "defaults": {"styles": ["bachata", "salsa"], "location": "Hatch Shell on the Esplanade"},
         "enabled": True},
    ]
    sources_path = tmp_path / "sources.json"
    write_json(sources_path, sources)
    monkeypatch.setattr(su, "SOURCES_PATH", sources_path)
    monkeypatch.setattr(su, "SCRAPED_DIR", tmp_path / "scraped")
    monkeypatch.setattr(su, "SCRAPER_HEALTH_PATH", tmp_path / "scraper-health.json")
    return tmp_path


# ── run_scraper ──────────────────────────────────────────────────────

def test_run_scraper_success_writes_file_and_records_health(registry, capsys):
    events = [future_event("beatrice-calendar")]
    rc = su.run_scraper("beatrice-calendar", lambda source: events)
    assert rc == 0
    written = read_json(su.scraped_path("beatrice-calendar"))
    assert [e["id"] for e in written] == [events[0]["id"]]
    health = su.load_scrape_health()["beatrice-calendar"]
    assert health["status"] == "ok"
    assert health["raw_found"] == 1 and health["kept"] == 1
    assert "[beatrice-calendar] ok: raw=1 kept=1" in capsys.readouterr().err


def test_run_scraper_filters_past_events_but_health_sees_raw(registry):
    events = [future_event("beatrice-calendar"), future_event("beatrice-calendar", days_ahead=-400)]
    assert su.run_scraper("beatrice-calendar", lambda source: events) == 0
    assert len(read_json(su.scraped_path("beatrice-calendar"))) == 1
    health = su.load_scrape_health()["beatrice-calendar"]
    assert health["raw_found"] == 2 and health["kept"] == 1


def test_run_scraper_failure_keeps_stale_file_and_exits_1(registry, capsys):
    stale = [future_event("beatrice-calendar", name="Last week's good scrape")]
    write_json(su.scraped_path("beatrice-calendar"), stale)

    def broken(source):
        raise requests.ConnectionError("calendar.google.com unreachable")

    rc = su.run_scraper("beatrice-calendar", broken)
    assert rc == 1
    assert read_json(su.scraped_path("beatrice-calendar")) == stale, "a stale file beats an empty one"
    health = su.load_scrape_health()["beatrice-calendar"]
    assert health["status"] == "fetch_error"
    assert "ConnectionError" in health["note"]
    err = capsys.readouterr().err
    assert "Traceback" in err and "FAILED" in err


def test_run_scraper_failure_with_no_existing_file_writes_nothing(registry):
    def broken(source):
        raise RuntimeError("markup exploded")
    assert su.run_scraper("beatrice-calendar", broken) == 1
    assert not su.scraped_path("beatrice-calendar").exists()


def test_run_scraper_disabled_source_is_a_noop(registry, capsys):
    stale = [future_event("retired-source")]
    write_json(su.scraped_path("retired-source"), stale)
    calls = []
    rc = su.run_scraper("retired-source", lambda source: calls.append(source) or [])
    assert rc == 0
    assert calls == [], "fetch must not run for a disabled source"
    assert read_json(su.scraped_path("retired-source")) == stale
    assert "retired-source" not in su.load_scrape_health()
    assert "disabled" in capsys.readouterr().err


def test_run_scraper_unknown_source_returns_1(registry):
    assert su.run_scraper("no-such-source", lambda source: []) == 1


def test_run_scraper_skipped_leaves_file_and_records_skipped(registry):
    stale = [future_event("beatrice-calendar")]
    write_json(su.scraped_path("beatrice-calendar"), stale)

    def skip(source):
        raise su.ScraperSkipped("nothing to do today")

    assert su.run_scraper("beatrice-calendar", skip) == 0
    assert read_json(su.scraped_path("beatrice-calendar")) == stale
    assert su.load_scrape_health()["beatrice-calendar"]["status"] == "skipped"


def test_run_scraper_structure_missing_when_raw_is_zero(registry):
    rc = su.run_scraper("beatrice-calendar", lambda s: su.ScrapeResult([], raw_found=0))
    assert rc == 0
    assert su.load_scrape_health()["beatrice-calendar"]["status"] == "structure_missing"


def test_scrape_ics_main_runs_end_to_end_through_the_runner(registry, monkeypatch):
    import scrape_ics
    start = (datetime.now(su.NY_TZ) + timedelta(days=10)).strftime("%Y%m%dT200000")
    ics = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\n"
           f"DTSTART;TZID=America/New_York:{start}\nSUMMARY:Salsa Night\n"
           "END:VEVENT\nEND:VCALENDAR\n")
    monkeypatch.setattr(scrape_ics, "fetch", lambda url, **kw: response(ics, content_type="text/calendar"))
    assert scrape_ics.main(["timba-messengers"]) == 0
    written = read_json(su.scraped_path("timba-messengers"))
    assert [e["name"] for e in written] == ["Salsa Night"]
    assert su.load_scrape_health()["timba-messengers"]["status"] == "ok"


# ── scraper_commands / run_scrapers ──────────────────────────────────

def test_scraper_commands_come_from_sources_json_only(registry):
    commands = su.scraper_commands()
    ids = [sid for sid, _ in commands]
    assert ids == ["beatrice-calendar", "timba-messengers", "bobas", "submissions"]
    assert "retired-source" not in ids and "manual-only" not in ids
    for sid, argv in commands:
        assert argv[0] == sys.executable
        assert argv[-1] == sid
    by_id = dict(commands)
    assert by_id["timba-messengers"][1].endswith("scrape_ics.py")
    assert by_id["bobas"][1].endswith("scrape_facebook.py")
    assert by_id["submissions"][1].endswith("fetch_submissions.py")


def test_scraper_commands_only_filter(registry):
    assert [sid for sid, _ in su.scraper_commands(only="timba-messengers")] == ["timba-messengers"]
    assert su.scraper_commands(only="retired-source") == []


def test_scraper_commands_does_not_duplicate_a_listed_submissions_source(registry):
    sources = read_json(su.SOURCES_PATH)
    sources.append({"id": "submissions", "scraper": "fetch_submissions.py", "enabled": True})
    write_json(su.SOURCES_PATH, sources)
    ids = [sid for sid, _ in su.scraper_commands()]
    assert ids.count("submissions") == 1


def test_run_scrapers_reports_one_dict_per_source(monkeypatch):
    import run_pipeline
    fake = [
        ("good", [sys.executable, "-c", "print('scraped')"]),
        ("bad", [sys.executable, "-c", "import sys; sys.stderr.write('markup gone\\n'); sys.exit(3)"]),
        ("slow", [sys.executable, "-c", "import time; time.sleep(5)"]),
    ]
    monkeypatch.setattr(run_pipeline, "scraper_commands", lambda only=None: fake)
    results = run_pipeline.run_scrapers(timeout=1)
    assert [r["source_id"] for r in results] == ["good", "bad", "slow"]
    assert set(results[0]) == {"source_id", "ok", "returncode", "seconds", "stderr_tail"}
    assert results[0]["ok"] is True and results[0]["returncode"] == 0
    assert results[1]["ok"] is False and results[1]["returncode"] == 3
    assert "markup gone" in results[1]["stderr_tail"]
    assert results[2]["ok"] is False and "timeout" in results[2]["stderr_tail"]


# ── fetch(): retries, backoff, encoding ──────────────────────────────

def _install_get(monkeypatch, outcomes):
    """requests.get that pops one outcome per call: an exception to raise or a Response."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def test_fetch_retries_connection_errors_with_backoff(monkeypatch):
    slept = []
    monkeypatch.setattr(su.time, "sleep", slept.append)
    calls = _install_get(monkeypatch, [
        requests.ConnectionError("reset"), requests.Timeout("slow"), response("ok"),
    ])
    resp = su.fetch("https://example.test/feed", retries=3)
    assert resp.text == "ok"
    assert len(calls) == 3
    assert slept == [1.5, 3.0], "linear backoff between attempts"


def test_fetch_retries_5xx_then_gives_up_with_the_last_error(monkeypatch):
    calls = _install_get(monkeypatch, [response("down", 503)] * 4)
    with pytest.raises(requests.HTTPError):
        su.fetch("https://example.test/feed", retries=3)
    assert len(calls) == 4, "initial attempt plus three retries"


def test_fetch_does_not_retry_a_404(monkeypatch):
    calls = _install_get(monkeypatch, [response("gone", 404), response("never", 200)])
    with pytest.raises(requests.HTTPError):
        su.fetch("https://example.test/missing")
    assert len(calls) == 1


def test_fetch_sends_the_right_identity(monkeypatch):
    calls = _install_get(monkeypatch, [response("a"), response("b")])
    su.fetch("https://example.test/a")
    su.fetch("https://example.test/b", browser=True)
    assert calls[0][1]["headers"]["User-Agent"] == su.DEV_UA
    assert "andrewlbernal@gmail.com" in su.DEV_UA
    assert calls[1][1]["headers"]["User-Agent"] == su.BROWSER_UA
    assert calls[0][1]["timeout"] == 20


def test_fetch_decodes_utf8_when_the_charset_header_is_missing(monkeypatch):
    _install_get(monkeypatch, [response("Tambó at La Fábrica".encode("utf-8"))])
    resp = su.fetch("https://example.test/page")
    assert resp.encoding == "utf-8"
    assert resp.text == "Tambó at La Fábrica"


# ── Nominatim throttle ───────────────────────────────────────────────

def test_nominatim_gap_is_enforced_between_any_two_requests(monkeypatch):
    slept = []
    clock = [100.0]
    monkeypatch.setattr(su.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(su.time, "sleep", slept.append)
    monkeypatch.setattr(su, "_last_nominatim_at", 0.0)
    su._nominatim_throttle()
    assert slept == [], "first request goes straight through"
    clock[0] += 0.2
    su._nominatim_throttle()
    assert len(slept) == 1 and abs(slept[0] - (su.NOMINATIM_MIN_GAP - 0.2)) < 1e-6


def test_nominatim_user_agent_carries_a_contact(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return response("[]", content_type="application/json")
    monkeypatch.setattr(requests, "get", fake_get)
    assert su._nominatim_query("Nowhere, MA") is None
    assert seen["headers"]["User-Agent"] == su.DEV_UA


# ── Venue lookup (word boundaries, name segment only) ────────────────

@pytest.mark.parametrize("location", [
    "Marvelous Bar, Worcester, MA",            # "lous" inside Marvelous
    "The Anchorage Inn, Portsmouth NH",        # "the anchor" inside Anchorage
    "Julietta's Kitchen, Somerville, MA",      # "juliet" inside Julietta
    "Sparkle Lounge, 1 PKL Way, Boston",       # "pkl" only in the address part
])
def test_venue_lookup_rejects_substring_false_positives(location):
    assert su._lookup_venue(location) is None


@pytest.mark.parametrize("location, expected_key", [
    ("Lou's, 13 Brattle St, Cambridge, MA 02138", "lou's"),
    ("Lous Cambridge", "lous"),
    ("The Anchor, 1 Shipyard Park, Charlestown, MA", "the anchor"),
    ("PKL, 55 Congress St, Boston", "pkl"),
    ("Juliet, 257 Washington St, Somerville", "juliet"),
    ("Havana Club", "havana club"),
    ("Salsa night at 13 Brattle Street, Cambridge", "13 brattle street"),
    ("Magazine Beach, Cambridge", "magazine beach, cambridge"),
])
def test_venue_lookup_keeps_real_venues_and_addresses(location, expected_key):
    assert su._lookup_venue(location) == su.VENUE_COORDS[expected_key]


# ── parse_month_day / resolve_year rollover ──────────────────────────

def test_parse_month_day_rolls_past_dates_to_next_year():
    winter = date(2026, 12, 20)
    assert su.parse_month_day("Friday January 5 - Social", winter) == date(2027, 1, 5)
    assert su.parse_month_day("Dec 18 party", winter) == date(2026, 12, 18)  # within grace
    assert su.parse_month_day("Sept. 3rd social", winter, max_ahead_days=None) == date(2027, 9, 3)


def test_parse_month_day_drops_stale_and_invalid_dates():
    assert su.parse_month_day("March 1st social", date(2026, 8, 20)) is None  # ~7 months out
    assert su.parse_month_day("February 30", TODAY) is None
    assert su.parse_month_day("no date here", TODAY) is None


# ── ICS: floating UNTIL keeps the last occurrence (item 7) ───────────

def test_fix_rrule_until_converts_floating_eastern_to_utc():
    from scrape_ics import _fix_rrule_until
    # 23:59:59 EDT is 03:59:59 UTC the next day — not 23:59:59Z.
    assert (_fix_rrule_until("FREQ=WEEKLY;UNTIL=20260929T235959;BYDAY=TU")
            == "FREQ=WEEKLY;UNTIL=20260930T035959Z;BYDAY=TU")
    assert _fix_rrule_until("FREQ=WEEKLY;UNTIL=20260625") == "FREQ=WEEKLY;UNTIL=20260626T035959Z"
    already_utc = "FREQ=WEEKLY;UNTIL=20260929T235959Z"
    assert _fix_rrule_until(already_utc) == already_utc


def test_ics_series_with_floating_until_includes_the_last_occurrence():
    import scrape_ics
    events = scrape_ics.parse_ics_feed(fixture("calendar.ics"), source_id="beatrice-calendar", now=NOW)
    series = next(e for e in events if e["name"] == "Bachata Tuesdays")
    assert series["recurring"] is True
    assert series["recurrences"][-1].startswith("2026-09-29T20:00"), (
        "an 8pm Eastern occurrence on the UNTIL day must survive; appending Z would drop it")
    assert len(series["recurrences"]) == 5


# ── Facebook: no raw input must not wipe the normalized file (item 2) ─

def test_facebook_without_raw_input_leaves_file_untouched(registry, monkeypatch, capsys):
    import scrape_facebook
    monkeypatch.setattr(scrape_facebook, "SCRAPED_DIR", su.SCRAPED_DIR)
    stale = [future_event("bobas", name="Last agent scrape")]
    write_json(su.scraped_path("bobas"), stale)
    assert not scrape_facebook.raw_input_path("bobas").exists()

    assert scrape_facebook.main(["bobas"]) == 0
    assert read_json(su.scraped_path("bobas")) == stale
    assert su.load_scrape_health()["bobas"]["status"] == "skipped"
    out = capsys.readouterr()
    assert "No raw events file" in out.out and "bobas-raw.json" in out.out


def test_facebook_normalizes_the_default_raw_file(registry, monkeypatch):
    import scrape_facebook
    monkeypatch.setattr(scrape_facebook, "SCRAPED_DIR", su.SCRAPED_DIR)
    when = datetime.now(su.NY_TZ) + timedelta(days=20)
    raw = [{"name": "Bachata on the Docks", "date": when.strftime("%B %d, %Y"),
            "time": "6:00 PM", "end_time": "9:00 PM",
            "url": "https://www.facebook.com/events/1/"}]
    write_json(scrape_facebook.raw_input_path("bobas"), raw)
    assert scrape_facebook.main(["bobas"]) == 0
    written = read_json(su.scraped_path("bobas"))
    assert len(written) == 1
    ev = written[0]
    assert ev["name"] == "Bachata on the Docks"
    assert ev["startDate"].startswith(when.strftime("%Y-%m-%dT18:00"))
    assert ev["location"] == "Hatch Shell on the Esplanade"  # source default
    assert ev["styles"] == ["bachata"]


def test_facebook_bare_empty_raw_is_rejected_and_preserves_last_good_data(registry, monkeypatch):
    import scrape_facebook
    monkeypatch.setattr(scrape_facebook, "SCRAPED_DIR", su.SCRAPED_DIR)
    write_json(scrape_facebook.raw_input_path("bobas"), [])
    old = [future_event("bobas", name="Old listing")]
    write_json(su.scraped_path("bobas"), old)

    assert scrape_facebook.main(["bobas"]) == 1
    assert read_json(su.scraped_path("bobas")) == old
    health = su.load_scrape_health()["bobas"]
    assert health["status"] == "fetch_error"
    assert "bare []" in health["note"]


def test_facebook_no_upcoming_envelope_is_healthy_evidence(registry, monkeypatch):
    import scrape_facebook
    monkeypatch.setattr(scrape_facebook, "SCRAPED_DIR", su.SCRAPED_DIR)
    capture = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_url": "https://www.facebook.com/bobas/events",
        "status": "no_upcoming",
        "events": [],
    }
    write_json(scrape_facebook.raw_input_path("bobas"), capture)
    write_json(su.scraped_path("bobas"), [future_event("bobas", name="Old listing")])

    assert scrape_facebook.main(["bobas"]) == 0
    assert read_json(su.scraped_path("bobas")) == []
    health = su.load_scrape_health()["bobas"]
    assert health["status"] == "skipped"
    assert "browser evidence confirmed" in health["note"]


def test_facebook_captured_envelope_normalizes_events(registry, monkeypatch):
    import scrape_facebook
    monkeypatch.setattr(scrape_facebook, "SCRAPED_DIR", su.SCRAPED_DIR)
    when = datetime.now(su.NY_TZ) + timedelta(days=20)
    capture = {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_url": "https://www.facebook.com/bobas/events",
        "status": "captured",
        "events": [{
            "name": "Salsa by the River",
            "date": when.strftime("%B %d, %Y"),
            "time": "7:00 PM",
            "url": "https://www.facebook.com/events/2/",
        }],
    }
    write_json(scrape_facebook.raw_input_path("bobas"), capture)

    assert scrape_facebook.main(["bobas"]) == 0
    assert read_json(su.scraped_path("bobas"))[0]["name"] == "Salsa by the River"
    health = su.load_scrape_health()["bobas"]
    assert health["status"] == "ok"
    assert "browser evidence captured" in health["note"]


def test_facebook_stale_envelope_is_rejected(registry, monkeypatch):
    import scrape_facebook
    monkeypatch.setattr(scrape_facebook, "SCRAPED_DIR", su.SCRAPED_DIR)
    capture = {
        "schema_version": 1,
        "checked_at": (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
        "source_url": "https://www.facebook.com/bobas/events",
        "status": "no_upcoming",
        "events": [],
    }
    write_json(scrape_facebook.raw_input_path("bobas"), capture)

    assert scrape_facebook.main(["bobas"]) == 1
    assert su.load_scrape_health()["bobas"]["status"] == "fetch_error"


def test_facebook_yearless_date_rolls_forward_not_back():
    import scrape_facebook
    today = date(2026, 12, 20)
    jan = scrape_facebook._parse_fb_datetime("Fri, Jan 10", "8:00 PM", today=today)
    assert (jan.year, jan.month, jan.day, jan.hour) == (2027, 1, 10, 20)
    recent = scrape_facebook._parse_fb_datetime("Dec 18", "8:00 PM", today=today)
    assert recent.year == 2026
    explicit = scrape_facebook._parse_fb_datetime("January 10, 2025", "8:00 PM", today=today)
    assert explicit.year == 2025, "an explicit year is never rewritten"
    assert not hasattr(scrape_facebook, "_parse_fb_time_range")


# ── One fixture parse per scraper ────────────────────────────────────

def test_scrape_ics_fixture():
    import scrape_ics
    events = scrape_ics.parse_ics_feed(fixture("calendar.ics"), source_id="beatrice-calendar", now=NOW)
    by_name = {e["name"]: e for e in events}
    assert set(by_name) == {"Salsa Social at Havana Club", "Bachata Tuesdays", "Contra Dance"}
    havana = by_name["Salsa Social at Havana Club"]
    assert havana["startDate"] == "2026-09-05T21:00:00-04:00"
    assert havana["endDate"] == "2026-09-06T01:00:00-04:00"
    assert havana["location"] == "Havana Club, 288 Green St, Cambridge, MA"
    assert havana["url"] == "https://example.com/havana"
    assert havana["cost"].startswith("$15")  # extract_cost keeps the matched phrase ("$15 cover")
    assert "salsa" in havana["styles"] and "bachata" in havana["styles"]
    assert havana["dayOfWeek"] == "Saturday"


def test_scrape_keyword_calendar_fixture(registry, monkeypatch):
    import scrape_ics
    import scrape_keyword_calendar as kw
    monkeypatch.setattr(kw, "parse_ics_feed", functools.partial(scrape_ics.parse_ics_feed, now=NOW))
    monkeypatch.setattr(kw, "fetch", lambda url, **k: response(fixture("calendar.ics"), content_type="text/calendar"))
    result = kw.fetch_source({"id": "somerville-arts", "url": "https://example.test/events/", "tribe_ical": True})
    assert result.raw_found == 3
    assert sorted(e["name"] for e in result.events) == ["Bachata Tuesdays", "Salsa Social at Havana Club"]
    assert kw.resolve_feed_url({"url": "https://x.test/events/", "tribe_ical": True}) == "https://x.test/events/?ical=1"


def test_scrape_tribe_calendar_fixture(monkeypatch):
    import scrape_ics
    import scrape_tribe_calendar as tribe
    monkeypatch.setattr(tribe, "parse_ics_feed", functools.partial(scrape_ics.parse_ics_feed, now=NOW))
    pages = {
        "https://somervilleartscouncil.org/events/": fixture("tribe_listing.html"),
        "https://somervilleartscouncil.org/events/salsa-in-the-park/ical/": fixture("tribe_event.ics"),
    }
    monkeypatch.setattr(tribe, "_fetch", lambda url, timeout=30: pages[url])
    source = {"id": "somerville-arts", "url": "https://somervilleartscouncil.org/events/",
              "event_path_prefix": "https://somervilleartscouncil.org/events/"}
    result = tribe.fetch_source(source)
    assert result.raw_found == 1, "category/list chrome links and duplicates are not events"
    (ev,) = result.events
    assert ev["name"] == "Salsa in the Park"
    assert ev["startDate"] == "2026-09-13T16:00:00-04:00"
    assert ev["location"] == "Seven Hills Park, Somerville, MA"
    assert ev["url"] == "https://somervilleartscouncil.org/events/salsa-in-the-park/"
    assert (ev["lat"], ev["lng"]) == (42.397751, -71.124514), "GEO from the iCal, not re-geocoded"
    assert "<b>" not in ev["description"] and "&amp;" not in ev["description"]


def test_scrape_jsonld_fixture(monkeypatch):
    import scrape_jsonld
    pages = {
        "https://www.listerevents.com/events": fixture("wix_listing.html"),
        "https://www.listerevents.com/event-details/salsa-night-at-lister": fixture("wix_event.html"),
    }
    monkeypatch.setattr(scrape_jsonld, "_fetch", lambda url, browser, timeout=20: pages[url])
    monkeypatch.setattr(scrape_jsonld.time, "sleep", lambda s: None)
    source = {"id": "lister-events", "url": "https://www.listerevents.com/events",
              "link_pattern": "/event-details/", "id_prefix": "lister",
              "detail_description": "wix", "enabled": True}
    result = scrape_jsonld.fetch_source(source, now=NOW)
    assert result.raw_found == 1
    (ev,) = result.events
    assert ev["id"] == "lister-salsa-night-at-lister"
    assert ev["name"] == "Salsa Night at Lister"
    assert ev["startDate"] == "2026-09-12T21:00:00-04:00"
    assert ev["location"] == "Dante Alighieri Society, 41 Hampshire St, Cambridge, MA, 02139"
    assert ev["cost"] == "$20"
    assert "Beginner lesson at 8pm" in ev["description"], "the fuller Wix 'About the event' text wins"
    assert set(ev["styles"]) == {"salsa", "bachata"}


def test_scrape_jsonld_unreachable_listing_raises(monkeypatch):
    import scrape_jsonld
    def down(url, browser, timeout=20):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(scrape_jsonld, "_fetch", down)
    monkeypatch.setattr(scrape_jsonld.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="no listing page could be fetched"):
        scrape_jsonld.fetch_source({"id": "lister-events", "url": "https://x.test/events",
                                    "link_pattern": "/event-details/"}, now=NOW)


def test_scrape_eventbrite_fixture():
    import scrape_eventbrite as eb
    urls = eb.extract_event_urls(fixture("eventbrite_search.html"))
    assert urls == {
        "https://www.eventbrite.com/e/boston-bachata-social-tickets-123456789",
        "https://www.eventbrite.com/e/salsa-sundays-tickets-987654321",
    }
    url = "https://www.eventbrite.com/e/boston-bachata-social-tickets-123456789"
    ev = eb.parse_event_page(fixture("eventbrite_event.html"), url)
    assert ev["id"] == "eb-123456789"
    assert ev["name"] == "Boston Bachata Social"
    assert ev["startDate"] == "2026-09-19T20:00:00-04:00"
    assert ev["location"] == "Havana Club\n288 Green St, Cambridge, MA"
    assert ev["cost"] == "$15"
    assert "Organizer: Bachata Boston" in ev["description"]
    assert "open dancing until midnight" in ev["description"], "long-form page text beats the JSON-LD summary"
    assert set(ev["styles"]) == {"bachata", "salsa"}


def test_scrape_eventbrite_drops_non_dance_pages():
    import scrape_eventbrite as eb
    html = fixture("eventbrite_event.html").replace("Bachata", "Pottery").replace("bachata", "pottery")
    html = re.sub(r"Salsa room\s+too\.", "Glazing demo too.", html)
    assert eb.parse_event_page(html, "https://www.eventbrite.com/e/x-tickets-1") is None


def test_scrape_fiesta_dance_fixture():
    import scrape_fiesta_dance as fiesta
    today = datetime(2026, 9, 1, 12, 0, tzinfo=su.NY_TZ)
    events = fiesta.parse_socials_page(fixture("fiesta_socials.html"), today=today)
    assert [e["startDate"][:10] for e in events] == ["2026-09-12", "2026-09-20"]
    first, second = events
    assert first["name"] == "Salsa & Bachata Social w/ Fiesta Dance Co"
    assert first["location"] == "Sol de Mexico, 350 E Main St, Milford, MA 01757"
    assert first["dayOfWeek"] == "Saturday"  # Sept 12 2026 is a Saturday; the page's weekday is not trusted
    assert second["location"].startswith("Agave Mexican Grill & Cantina, 197A Boston Post Rd W")
    assert first["id"] != second["id"]


def test_scrape_eastboston_fixture(monkeypatch):
    import scrape_eastboston as ebos
    raw = ebos.parse_listing(fixture("eastboston_listing.html"))
    assert [r["name"] for r in raw] == ["Salsa Night at Bremen Street Park", "Yoga in the Park"]
    assert raw[0]["start"].isoformat() == "2026-09-11T19:00:00-04:00"
    assert raw[0]["end"].isoformat() == "2026-09-11T22:00:00-04:00"

    monkeypatch.setattr(ebos, "fetch", lambda url, **k: response(fixture("eastboston_detail.html")))
    events = ebos.build_events(raw, {"defaults": {"location": "East Boston, MA"}}, delay=0)
    (ev,) = events
    assert ev["name"] == "Salsa Night at Bremen Street Park"
    assert ev["location"] == "Bremen Street Park Amphitheater, East Boston, MA"
    assert ev["startDate"] == "2026-09-11T19:00:00-04:00"
    assert ev["cost"] == "Free"
    assert "Date: Sep 11" not in ev["description"]


def test_scrape_lous_fixture():
    import scrape_lous as lous
    payload = json.loads(fixture("lous_payload.json"))
    events, raw = lous.parse_payload(payload)
    assert raw == 2, "health counts every upcoming item, danceable or not"
    (ev,) = events
    assert ev["name"] == "Latin Night – La Diáspora Combo"
    assert ev["artist"] == "La Diáspora Combo"
    assert ev["artistUrl"] == "https://www.instagram.com/ladiasporacombo/"
    assert ev["startDate"] == "2026-09-12T21:00:00-04:00"
    assert ev["location"] == lous.VENUE
    assert (ev["lat"], ev["lng"]) == (lous.VENUE_LAT, lous.VENUE_LNG)
    assert ev["url"] == "https://www.wearelous.com/lous-live/latin-night-diaspora-combo"
    assert "Reservation" not in ev["description"]
    assert set(ev["styles"]) >= {"salsa", "merengue"}


# ── Shared constants really are shared ───────────────────────────────

def test_scrapers_share_one_ua_one_clock_one_keyword_rule():
    import link_meta
    import scrape_eastboston, scrape_eventbrite, scrape_fiesta_dance, scrape_ics, scrape_jandl, scrape_lous
    for mod in (scrape_fiesta_dance, scrape_ics, scrape_jandl, scrape_lous):
        assert mod.NY_TZ is su.NY_TZ
    assert link_meta.BROWSER_UA is su.BROWSER_UA
    assert link_meta.LOCAL_TZ is su.NY_TZ
    assert scrape_eastboston.is_dance_relevant("Salsa night") and scrape_eventbrite.is_dance_relevant("Cumbia", "")
    assert not scrape_eastboston.is_dance_relevant("Yoga in the park")
    assert scrape_lous.DANCE_NIGHT_RE is su.DANCE_NIGHT_RE
