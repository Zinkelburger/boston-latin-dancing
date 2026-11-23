"""A series that has ended must leave the map, and stay off it.

Two listings were still on the map on 2026-11-23, in the cold and the dark:

  "Rueda in the Pahk"            its calendar closed the series on Nov 1 (RRULE
                                 UNTIL), so it dropped out of the scrape, and the
                                 store kept the Sundays it had projected through
                                 January while the rule was still open.
  "Egleston Park Summer Social"  a calendar entry repeating forever, listed for
                                 Dec 20 in an outdoor garden while the
                                 organizer's last page was for Sep 20.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es
import scraper_utils as su
import verify_events
from event_store.archive import ENDED_HOLD_DAYS, HOLD_ENDED, ended_hold_lifts
from event_store.series import trim_series_after

NY = ZoneInfo("America/New_York")
OLD_PAGE = "https://example.com/socials-september"


def _at(days: int, hour: int = 18) -> datetime:
    return (datetime.now(NY) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def _series(**overrides) -> dict:
    nights = [_at(d) for d in (-21, -14, -7, 7, 14, 21)]
    base = {
        "id": "rueda-uid", "name": "Rueda in the Pahk", "startDate": nights[0].isoformat(),
        "endDate": (nights[0] + timedelta(hours=3)).isoformat(), "dayOfWeek": "Sunday",
        "location": "Jill Brown Rhone Park, 389 Massachusetts Ave, Cambridge, MA",
        "lat": 42.36, "lng": -71.10, "url": OLD_PAGE, "styles": ["salsa"],
        "recurring": True, "recurrences": [n.isoformat() for n in nights],
        "source": "beatrice-calendar",
    }
    base.update(overrides)
    return base


def _closed(source_id: str, rows: list[dict]) -> None:
    su.ended_series_path(source_id).write_text(json.dumps(rows), encoding="utf-8")


# ── the calendar closed it ────────────────────────────────────────────

def test_trimming_leaves_the_series_on_its_last_real_night():
    ev = _series()
    assert trim_series_after(ev, _at(-7).date()) is True
    assert [r[:10] for r in ev["recurrences"]] == [_at(d).date().isoformat() for d in (-21, -14, -7)]
    assert ev["startDate"][:10] == _at(-7).date().isoformat()
    assert datetime.fromisoformat(ev["endDate"]) - datetime.fromisoformat(ev["startDate"]) == timedelta(hours=3)
    assert trim_series_after(ev, _at(-7).date()) is False


def test_a_series_holding_only_later_nights_ends_on_the_sources_last_night():
    ev = _series(startDate=_at(7).isoformat(), endDate=_at(7, 21).isoformat(),
                 recurrences=[_at(7).isoformat(), _at(14).isoformat()])
    assert trim_series_after(ev, _at(-7).date()) is True
    assert ev["recurrences"] == [ev["startDate"]]
    assert ev["startDate"][:13] == _at(-7).isoformat()[:13]


def test_a_series_its_calendar_closed_stops_projecting_and_is_archived():
    es.save_active([_series(), _series(id="other-uid", name="Kiz Thursday")])
    (su.SCRAPED_DIR / "beatrice-calendar.json").write_text("[]", encoding="utf-8")
    _closed("beatrice-calendar", [{"id": "rueda-uid", "name": "Rueda in the Pahk",
                                   "lastDate": _at(-7).isoformat()}])
    result = es.ingest_scraped()
    assert result["series_ended"] == 1
    es.archive_past_events()
    assert [e["id"] for e in es.load_active()] == ["other-uid"]
    (archived,) = es.load_archive()
    assert max(r[:10] for r in archived["recurrences"]) == _at(-7).date().isoformat()


def test_a_series_that_runs_on_under_a_new_calendar_entry_is_not_ended():
    # Kiz Thursday (2026-11-23): the entry our record took its id from was
    # closed in July; the calendar carries the same night under a new UID.
    es.save_active([_series()])
    renewed = _series(id="new-uid", startDate=_at(7).isoformat(), endDate=_at(7, 21).isoformat(),
                      recurrences=[_at(d).isoformat() for d in (7, 14, 21, 28)])
    (su.SCRAPED_DIR / "beatrice-calendar.json").write_text(json.dumps([renewed]), encoding="utf-8")
    _closed("beatrice-calendar", [{"id": "rueda-uid", "lastDate": _at(-7).isoformat()}])
    assert es.ingest_scraped()["series_ended"] == 0
    (kept,) = es.load_active()
    assert kept["id"] == "rueda-uid"
    assert max(r[:10] for r in kept["recurrences"]) == _at(28).date().isoformat()


def test_another_sources_record_with_that_id_is_left_alone():
    es.save_active([_series(source="sensualeros-boston")])
    (su.SCRAPED_DIR / "beatrice-calendar.json").write_text("[]", encoding="utf-8")
    _closed("beatrice-calendar", [{"id": "rueda-uid", "lastDate": _at(-7).isoformat()}])
    assert es.ingest_scraped()["series_ended"] == 0
    assert len(es.load_active()[0]["recurrences"]) == 6


def test_the_ended_file_is_not_read_as_events():
    (su.SCRAPED_DIR / "beatrice-calendar.json").write_text("[]", encoding="utf-8")
    _closed("beatrice-calendar", [{"id": "rueda-uid", "name": "Rueda in the Pahk",
                                   "lastDate": _at(-7).isoformat()}])
    result = es.ingest_scraped()
    assert result["files_processed"] == 1 and result["added"] == 0
    assert es.load_active() == [] and es.load_pending() == []


# ── its source still lists it, but it is over ─────────────────────────

def _incoming(url: str) -> dict:
    return _series(startDate=_at(7).isoformat(), endDate=_at(7, 21).isoformat(),
                   recurrences=[_at(7).isoformat(), _at(14).isoformat()], url=url)


def test_an_ended_series_is_not_pulled_back_by_the_same_listing():
    es.save_active([_series()])
    es.archive_event("rueda-uid", reason="out of season", hold=HOLD_ENDED,
                     seen_links=["https://example.com/socials-july"])
    for url in (OLD_PAGE, "https://example.com/socials-july"):
        assert es.add_event(_incoming(url))["status"] == "duplicate"
    assert es.load_active() == []


def test_an_ended_series_comes_back_with_a_page_nobody_had_seen():
    es.save_active([_series()])
    es.archive_event("rueda-uid", reason="out of season", hold=HOLD_ENDED)
    assert es.add_event(_incoming("https://example.com/socials-next-summer"))["status"] == "reactivated"
    (back,) = es.load_active()
    assert "_archive_hold" not in back and "_hold_seen_links" not in back


def test_an_ended_hold_gets_another_look_after_a_few_months():
    archived = {"archivedAt": datetime.now(timezone.utc).isoformat(), "_hold_seen_links": [OLD_PAGE]}
    incoming = {"url": OLD_PAGE}
    assert ended_hold_lifts(archived, incoming) is False
    later = datetime.now(timezone.utc) + timedelta(days=ENDED_HOLD_DAYS + 1)
    assert ended_hold_lifts(archived, incoming, now=later) is True


# ── noticing it: the series' own page is for a night long past ────────

def test_a_series_whose_page_is_months_old_is_questioned():
    ev = _series()
    note, next_listed = verify_events.stale_series_page(ev, _at(-64).date().isoformat())
    assert "64 days ago" in note and next_listed == _at(7).isoformat()


def test_a_recent_page_a_one_off_and_a_finished_series_are_not():
    recent = _at(-20).date().isoformat()
    old = _at(-64).date().isoformat()
    assert verify_events.stale_series_page(_series(), recent) is None
    assert verify_events.stale_series_page(_series(recurring=False, recurrences=[]), old) is None
    over = _series(recurrences=[_at(-21).isoformat(), _at(-14).isoformat()])
    assert verify_events.stale_series_page(over, old) is None


def test_checked_and_still_running_stands_for_a_month():
    old = _at(-64).date().isoformat()
    checked = _series(_series_running_checked_at=datetime.now(timezone.utc).isoformat())
    assert verify_events.stale_series_page(checked, old) is None
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    assert verify_events.stale_series_page(_series(_series_running_checked_at=long_ago), old) is not None
