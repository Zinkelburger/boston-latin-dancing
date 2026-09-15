"""Headless Facebook capture: parsing rendered pages into evidence and signals."""

from datetime import date, datetime

import pytest

import event_doctor as doctor
import fetch_facebook as ff
import scrape_facebook

TODAY = date(2026, 9, 15)
NOW = datetime(2026, 9, 15, 13, 0, tzinfo=ff.NY_TZ)

CHROME = 'class="x1i10hfl xjbqb8w"'  # the noise every real anchor carries


def _events_tab(cards: list[str], upcoming: bool) -> str:
    tabs = "Upcoming Past More Upcoming Past" if upcoming else "Past More Past"
    body = "".join(cards)
    return (
        "<html><head><title>Tambó Salsa | Facebook</title></head><body>"
        f"<div>Log In</div><span>Tambó Salsa</span> <span>Events</span> <span>{tabs}</span>"
        f"{body}<div>See more on Facebook</div></body></html>" + "x" * 60_000
    )


def _card(event_id: str, when: str, name: str, venue: str, city: str, host: str) -> str:
    return (
        f'<a {CHROME} href="https://www.facebook.com/events/{event_id}/?ref=1"><img></a>'
        f"<span>{when}</span><a {CHROME} href=\"https://www.facebook.com/events/{event_id}/\">"
        f"<span>{name}</span></a><span>{venue} · {city}</span><span>Event by {host}</span>"
    )


def _event_page(name: str, when: str, blurb: str, venue: str, street: str) -> str:
    return (
        f'<html><head><meta property="og:title" content="{name}" />'
        f'<meta property="og:description" content="Event in Cambridge by Tambó Salsa on '
        f'Friday, October 30 2026" /></head><body>'
        f"<span>30</span><span>{when}</span><h1>{name}</h1>"
        f"<span>Public · Anyone on or off Facebook</span><div>{blurb}… See more</div>"
        f"<span>Cambridge, Massachusetts</span><span>{venue}</span><span>{street}, United States</span>"
        f"<span>Host</span></body></html>" + "x" * 60_000
    )


ROOT_PAGE = (
    "<html><body><span>Tambó Salsa</span> Privacy · Terms · Cookies · More "
    '<img alt="May be an image of dancing and text that says \'Aleksei Krotov TAMBÓ SALSA SOCIAL\'">'
    "<span>Album Tambo Salsa Social 2026/09/11 Tambó Salsa added 49 new photos. 1h · Shared with Public</span>"
    "<span>All reactions: 1 Like Comment</span><span>See more from Tambó Salsa | Cambridge MA</span>"
    "</body></html>" + "x" * 60_000
)
ALBUMS_PAGE = (
    "<html><body><span>Tagged photos</span> <span>Albums</span> "
    "<span>Tambo Salsa Social 2026/09/25</span> <span>12 Items</span>"
    "<span>Photos</span> <span>525 Items</span>"
    "<span>Tambo Salsa Social 2026/07/31</span> <span>107 Items</span>"
    "<span>Cover photos</span> <span>4 Items</span>"
    "<span>See more on Facebook</span></body></html>" + "x" * 60_000
)
PHOTOS_PAGE = (
    "<html><body>"
    '<img alt="May be an image of dancing and text that says \'FUEGO Y CANDELA 2026 Schedule '
    "October 1oth- Saturday November 14th Saturday December 19th- Saturday *all dates are subject to change*'\">"
    '<img aria-label="May be an image of dancing and text that says \'Aleksei Krotov SALSA SOCIAL\'">'
    '<img alt="May be an image of 3 people">'
    "</body></html>" + "x" * 60_000
)

TAMBO = {
    "id": "tambo-salsa-fb",
    "type": "facebook",
    "enabled": True,
    "name": "Tambó Salsa Social",
    "facebook_events_url": "https://www.facebook.com/Tambosalsa/events",
    "defaults": {"location": "Dante Alighieri Society, 41 Hampshire St, Cambridge, MA 02139"},
}


# ── URL shapes ───────────────────────────────────────────────────────

def test_page_urls_for_slug_and_profile_pages():
    slug = ff.page_urls("https://www.facebook.com/Tambosalsa/events")
    assert slug["root"] == "https://www.facebook.com/Tambosalsa"
    assert slug["albums"] == "https://www.facebook.com/Tambosalsa/photos_albums"
    prof = ff.page_urls("https://www.facebook.com/profile.php?id=61551665503735&sk=events")
    assert prof["events"] == "https://www.facebook.com/profile.php?id=61551665503735&sk=events"
    assert prof["photos"].endswith("&sk=photos")


# ── dates in free text ───────────────────────────────────────────────

def test_dates_in_text_reads_numeric_named_and_ocr_mangled_forms():
    assert ff.dates_in_text("Tambo Salsa Social 2026/09/11", TODAY) == ["2026-09-11"]
    assert ff.dates_in_text("Dante's Salsa Inferno - 08/02/24", TODAY) == ["2024-08-02"]
    assert ff.dates_in_text("Sept 4th, 2026 Dante's SALSA", TODAY) == ["2026-09-04"]
    assert ff.dates_in_text(
        "2026 Schedule October 1oth- Saturday November 14th Saturday December 19th", TODAY
    ) == ["2026-10-10", "2026-11-14", "2026-12-19"]


def test_dates_in_text_rolls_a_yearless_date_months_behind_us_to_next_year():
    # Mid-September: "January 10" is next year, "August 7" is this year.
    assert ff.dates_in_text("January 10 party", TODAY) == ["2027-01-10"]
    assert ff.dates_in_text("August 7 party", TODAY) == ["2026-08-07"]
    assert ff.dates_in_text("no dates here", TODAY) == []


# ── Events tab ───────────────────────────────────────────────────────

def test_events_tab_cards_get_a_year_from_their_weekday():
    html = _events_tab([
        _card("100000000000111", "Fri, Oct 30 at 8:30 PM EDT", "Halloween Costume Party", "Tambó Salsa",
              "Cambridge", "Tambó Salsa"),
        _card("200000000000222", "Fri, Sep 4", "Sept 4th Social", "The Dante", "Cambridge", "Dante's Salsa Inferno"),
    ], upcoming=True)
    tab = ff.parse_events_tab(html, TODAY)
    assert tab.has_upcoming_tab is True
    assert [(c.event_id, c.day.isoformat(), c.time) for c in tab.cards] == [
        ("100000000000111", "2026-10-30", "8:30 PM"),
        ("200000000000222", "2026-09-04", ""),
    ]
    first = tab.cards[0]
    assert first.name_guess == "Halloween Costume Party Tambó Salsa"
    assert first.city == "Cambridge"
    assert first.host == "Tambó Salsa"           # no HTML tag debris from the chunk boundary
    assert first.url == "https://www.facebook.com/events/100000000000111/"


def test_events_tab_without_header_is_a_capture_error_not_no_upcoming():
    login_wall = "<html><body>Log in to Facebook to see this page</body></html>" + "x" * 60_000
    with pytest.raises(ff.CaptureError):
        ff.parse_events_tab(login_wall, TODAY)


def test_capture_no_upcoming_needs_past_only_header():
    html = _events_tab([
        _card("200000000000222", "Fri, Sep 4", "Sept 4th Social", "The Dante", "Cambridge", "Dante's Salsa Inferno"),
    ], upcoming=False)
    pages = {"https://www.facebook.com/Tambosalsa/events": html}
    env = ff.capture_source(TAMBO, today=TODAY, now=NOW, renderer=lambda u: _page(pages, u))
    assert env["status"] == "no_upcoming"
    assert env["events"] == []
    assert env["capture"] == {"method": "headless-chrome", "cards_seen": 1, "upcoming_tab": False}
    # every signal render failed (no fixture) and said so, without failing the capture
    assert len(env["signals"]["errors"]) == 3


def test_capture_refuses_an_upcoming_tab_with_no_parsable_cards():
    html = _events_tab(['<a href="https://www.facebook.com/events/300000000000333/">garbled card</a>'], upcoming=True)
    with pytest.raises(ff.CaptureError, match="Upcoming tab shown"):
        ff.capture_source(TAMBO, today=TODAY, now=NOW,
                          renderer=lambda u: html if u.endswith("/events") else _fail(u))


def _fail(url):
    raise ff.CaptureError(f"no fixture for {url}")


def _page(pages, url):
    if url in pages:
        return pages[url]
    _fail(url)


# ── full capture ─────────────────────────────────────────────────────

def _tambo_pages() -> dict:
    return {
        "https://www.facebook.com/Tambosalsa/events": _events_tab([
            _card("1796270331376772", "Fri, Oct 30 at 8:30 PM EDT",
                  "Halloween Salsa/Bachata Costume Party, Friday 10/30", "Tambó Salsa",
                  "Cambridge", "Tambó Salsa"),
        ], upcoming=True),
        "https://www.facebook.com/events/1796270331376772/": _event_page(
            "Halloween Salsa/Bachata Costume Party, Friday 10/30",
            "Friday, October 30, 2026 at 8:30 PM EDT",
            "Calling all ghouls and goblins: costume contest with CASH PRIZES",
            "Tambó Salsa", "35 Hampshire St, Cambridge, MA 02139-1547",
        ),
        "https://www.facebook.com/Tambosalsa": ROOT_PAGE,
        "https://www.facebook.com/Tambosalsa/photos_albums": ALBUMS_PAGE,
        "https://www.facebook.com/Tambosalsa/photos": PHOTOS_PAGE,
    }


def test_capture_builds_envelope_from_event_page_and_signals():
    pages = _tambo_pages()
    env = ff.capture_source(TAMBO, today=TODAY, now=NOW, renderer=lambda u: _page(pages, u))

    assert env["schema_version"] == 1
    assert env["checked_at"] == "2026-09-15T13:00:00-04:00"
    assert env["source_url"] == "https://www.facebook.com/Tambosalsa/events"
    assert env["status"] == "captured"
    [event] = env["events"]
    assert event == {
        "name": "Halloween Salsa/Bachata Costume Party, Friday 10/30",
        "date": "October 30, 2026",
        "time": "8:30 PM",
        "location": "Tambó Salsa, 35 Hampshire St, Cambridge, MA 02139-1547",
        "url": "https://www.facebook.com/events/1796270331376772/",
        "description": "Calling all ghouls and goblins: costume contest with CASH PRIZES",
    }
    signals = env["signals"]
    assert signals["errors"] == []
    assert signals["latest_post"] == {
        "text": "Album Tambo Salsa Social 2026/09/11 Tambó Salsa added 49 new photos. 1h · Shared with Public",
        "dates": ["2026-09-11"],
    }
    assert signals["albums"] == [
        {"title": "Tambo Salsa Social 2026/09/25", "items": 12, "dates": ["2026-09-25"]},
        {"title": "Tambo Salsa Social 2026/07/31", "items": 107, "dates": ["2026-07-31"]},
    ]
    # the watermark is dropped, the schedule flyer kept with its OCR fixed
    assert signals["flyers"] == [{
        "text": "FUEGO Y CANDELA 2026 Schedule October 1oth- Saturday November 14th Saturday "
                "December 19th- Saturday *all dates are subject to change*",
        "dates": ["2026-10-10", "2026-11-14", "2026-12-19"],
    }]


def test_capture_falls_back_to_card_facts_when_event_page_fails():
    pages = _tambo_pages()
    del pages["https://www.facebook.com/events/1796270331376772/"]
    env = ff.capture_source(TAMBO, today=TODAY, now=NOW, renderer=lambda u: _page(pages, u))
    [event] = env["events"]
    assert event["name"] == "Halloween Salsa/Bachata Costume Party, Friday 10/30 Tambó Salsa"
    assert event["date"] == "October 30, 2026"
    assert event["time"] == "8:30 PM"
    assert event["location"] == TAMBO["defaults"]["location"]
    assert env["signals"]["errors"] == ["event 1796270331376772: no fixture for "
                                        "https://www.facebook.com/events/1796270331376772/"]


def test_envelope_normalizes_through_the_facebook_scraper(monkeypatch, tmp_path):
    pages = _tambo_pages()
    monkeypatch.setattr(ff, "SCRAPED_DIR", tmp_path)
    monkeypatch.setattr(ff, "SIGNALS_PATH", tmp_path / "facebook-signals.json")
    monkeypatch.setattr(ff, "render", lambda u: _page(pages, u))
    env = ff.write_capture(TAMBO, today=TODAY, now=NOW)

    status, raw, checked_at = scrape_facebook.validate_capture(env, TAMBO, now=NOW)
    assert status == "captured" and len(raw) == 1
    [normalized] = scrape_facebook._parse_raw_events(raw, "tambo-salsa-fb", TAMBO["defaults"])
    assert normalized["startDate"].startswith("2026-10-30T20:30:00")
    assert normalized["location"].startswith("Tambó Salsa, 35 Hampshire St")

    signals = ff.load_signals(tmp_path / "facebook-signals.json")
    assert signals["tambo-salsa-fb"]["upcoming_events"] == [{
        "name": "Halloween Salsa/Bachata Costume Party, Friday 10/30",
        "date": "October 30, 2026",
        "url": "https://www.facebook.com/events/1796270331376772/",
    }]
    assert signals["tambo-salsa-fb"]["albums"][0]["dates"] == ["2026-09-25"]


def test_scraper_runs_capture_first_and_keeps_old_envelope_when_it_fails(monkeypatch, tmp_path, capsys):
    raw = tmp_path / "tambo-salsa-fb-raw.json"
    ff.write_json(raw, {
        "schema_version": 1, "checked_at": "2026-09-14T16:42:37-04:00",
        "source_url": "https://www.facebook.com/Tambosalsa/events",
        "status": "no_upcoming", "events": [],
    })
    monkeypatch.setattr(scrape_facebook, "raw_input_path", lambda sid: raw)
    monkeypatch.setattr(ff, "browser_capture_enabled", lambda: True)

    def boom(source, **kw):
        raise ff.CaptureError("chrome returned 12 bytes")
    monkeypatch.setattr(ff, "write_capture", boom)

    result = scrape_facebook.fetch_source(TAMBO)
    assert result.skipped is True and result.events == []
    assert "headless capture failed (CaptureError: chrome returned 12 bytes)" in result.note
    assert "WARNING" in capsys.readouterr().err


def test_scraper_skips_capture_for_an_explicit_file(monkeypatch, tmp_path):
    raw = tmp_path / "hand.json"
    ff.write_json(raw, {
        "schema_version": 1, "checked_at": "2026-09-14T16:42:37-04:00",
        "source_url": "https://www.facebook.com/Tambosalsa/events",
        "status": "no_upcoming", "events": [],
    })
    monkeypatch.setattr(ff, "browser_capture_enabled", lambda: True)
    monkeypatch.setattr(ff, "write_capture", lambda *a, **k: pytest.fail("capture must not run"))
    result = scrape_facebook.fetch_source(TAMBO, from_file_path=raw)
    assert result.skipped is True and "headless" not in result.note


# ── doctor: unmatched dated signals ──────────────────────────────────

FUEGO = {
    "id": "dantes-salsa", "type": "facebook", "enabled": True,
    "name": "Fuego y Candela Salsa Socials",
    "facebook_events_url": "https://www.facebook.com/FuegoyCandelaSalsa/events",
    "defaults": {"location": "The Dante Alighieri Society of Massachusetts"},
}


def test_doctor_flags_future_signal_dates_without_an_event_and_credits_matches():
    signals = {
        "tambo-salsa-fb": {
            "latest_post": {"text": "Album Tambo Salsa Social 2026/09/11", "dates": ["2026-09-11"]},
            "albums": [{"title": "Tambo Salsa Social 2026/09/25", "items": 12, "dates": ["2026-09-25"]}],
            "flyers": [], "errors": [],
        },
        "dantes-salsa": {
            "latest_post": None, "albums": [],
            "flyers": [{"text": "FUEGO Y CANDELA 2026 Schedule October 10th Saturday November 14th",
                        "dates": ["2026-10-10", "2026-11-14"]}],
            "errors": ["photos: chrome timed out"],
        },
    }
    signals["sabor-latino"] = {
        "latest_post": None, "albums": [], "errors": [],
        "flyers": [{"text": "SABOR LATINO UPCOMING EVENTS SEPTEMBER 22 Salsa at The Grove",
                    "dates": ["2026-09-22"]}],
    }
    sabor = {"id": "sabor-latino", "type": "facebook", "enabled": True, "name": "Sabor Latino Boston",
             "facebook_events_url": "https://www.facebook.com/SaborLatinoBoston/events"}
    events = [{
        "id": "fuego-series", "name": "Fuego y Candela Social", "source": "sensualeros-boston",
        "location": "41 Hampshire St, Cambridge, MA 02139",
        "startDate": "2026-10-11T00:30:00+00:00",
        "recurrences": ["2026-10-11T00:30:00+00:00", "2026-11-15T01:30:00+00:00"],
    }, {
        "id": "grove-tue", "name": "Salsa Dancing at The Grove", "source": "beatrice-calendar",
        "location": "Grove Hall, Boston, MA", "startDate": "2026-09-22T19:00:00-04:00",
    }, {
        "id": "unrelated", "name": "Bachata Night", "source": "eventbrite-boston-latin",
        "location": "Somerville, MA", "startDate": "2026-09-25T21:00:00-04:00",
    }]
    issues, matched = doctor.facebook_signal_issues(signals, [TAMBO, FUEGO, sabor], events, TODAY)

    assert [(i["source_id"], i.get("date"), i["problem"]) for i in issues] == [
        ("dantes-salsa", None, "capture error: photos: chrome timed out"),
        ("tambo-salsa-fb", "2026-09-25", "dated Facebook signal with no event on the map"),
    ]
    assert issues[1]["signal"] == "album: Tambo Salsa Social 2026/09/25"
    # Sep 11 is in the past: history, not an issue. Both Fuego dates match the
    # series record by organizer name even though its source is the calendar;
    # the Grove night matches on a word the flyer and the event name share; the
    # unrelated Sep 25 bachata night does not satisfy the Tambó album.
    assert [(m["date"], m["event_id"]) for m in matched] == [
        ("2026-10-10", "fuego-series"), ("2026-11-14", "fuego-series"), ("2026-09-22", "grove-tue"),
    ]


def test_doctor_records_facebook_signals_check(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "load_sources", lambda: [TAMBO])
    monkeypatch.setattr(doctor, "scraper_commands", lambda: [])
    monkeypatch.setattr(doctor, "load_scrape_health", lambda: {})
    monkeypatch.setattr(doctor, "load_active", lambda: [])
    monkeypatch.setattr(doctor, "load_pending", lambda: [])
    monkeypatch.setattr(doctor, "load_rejected", lambda: [])
    monkeypatch.setattr(doctor, "duplicate_report", lambda events: [])
    monkeypatch.setattr(doctor, "REPORT_PATH", tmp_path / "verification-report.json")
    monkeypatch.setattr(doctor, "raw_input_path", lambda sid: tmp_path / "missing-raw.json")
    signals_path = tmp_path / "facebook-signals.json"
    ff.write_json(signals_path, {"tambo-salsa-fb": {
        "latest_post": None, "flyers": [], "errors": [],
        "albums": [{"title": "Tambo Salsa Social 2026/09/25", "items": 1, "dates": ["2026-09-25"]}],
    }})
    monkeypatch.setattr(doctor, "SIGNALS_PATH", signals_path)

    result = doctor.run_doctor(now=datetime(2026, 9, 15, 12, tzinfo=ff.NY_TZ), include_publish_preview=False)
    check = result["checks"]["facebook_signals"]
    assert check["status"] == "warning"
    assert check["items"][0]["date"] == "2026-09-25"
    assert any(w["check"] == "facebook_signals" for w in result["warnings"])
