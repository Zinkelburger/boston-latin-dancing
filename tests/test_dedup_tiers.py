"""Dedup tiers and the helpers they lean on.

  * normalize_name used to strip "/", "-" and "#" *before* the date and
    edition regexes ran, so "Salsa Social 9/12" became "salsa social 912"
    and three of those patterns could never match anything.
  * The cross-source recurring tier returned "certain" on venue + weekday +
    word overlap alone, which auto-merged "Havana Club Bachata Thursdays"
    into "Havana Club Salsa Thursdays" — two different nights at 288 Green St.
  * Occurrence lists mix +00:00 / -04:00 / -05:00 spellings of the same
    instant; sorting them as strings mis-ordered "last" and kept both.
  * Two copies of the same weekday helper and a second word-overlap
    implementation had drifted apart.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import event_store as es
import recurrence_utils
from event_store import collapse_recurring_series, dedup_confidence, last_occurrence, normalize_name

NY = ZoneInfo("America/New_York")


# ── 7. normalize_name strips dates and editions before punctuation ────

def test_slash_dates_are_removed_not_squashed():
    assert normalize_name("Salsa Social 9/12") == "salsa social"
    assert normalize_name("Salsa Social 09/12/2026") == "salsa social"
    assert normalize_name("Bachata Night 9-12") == "bachata night"


def test_edition_markers_are_removed():
    assert normalize_name("Kizomba Night Vol. 3") == "kizomba night"
    assert normalize_name("Salsa Party #4") == "salsa party"
    assert normalize_name("Sept 12 Salsa Social") == "salsa social"
    assert normalize_name("Salsa Social, Sep 12th") == "salsa social"


def test_normalize_name_still_drops_punctuation_and_case():
    assert normalize_name("¡Salsa & Bachata!  Social") == "salsa bachata social"


# ── 8. cross-source recurring tier needs more than venue + weekday ────

def _thursday(weeks_ahead: int, hour: int = 21) -> datetime:
    today = datetime.now(NY).replace(hour=hour, minute=0, second=0, microsecond=0)
    ahead = (4 - today.isoweekday()) % 7 + 7 * weeks_ahead
    return today + timedelta(days=ahead)


def _havana(**overrides):
    start = _thursday(1)
    base = {
        "id": "havana-bachata",
        "name": "Havana Club Bachata Thursdays",
        "startDate": start.isoformat(),
        "endDate": (start + timedelta(hours=4)).isoformat(),
        "location": "288 Green St, Cambridge, MA 02139",
        "lat": 42.3649,
        "lng": -71.1035,
        "recurring": True,
        "dayOfWeek": "Thursday",
        "styles": ["bachata"],
        "source": "sensualeros-boston",
        "url": "https://example.com/a",
    }
    base.update(overrides)
    return base


def test_different_styles_at_the_same_venue_and_weekday_are_review_not_certain():
    bachata = _havana()
    salsa = _havana(id="havana-salsa", name="Havana Club Salsa Thursdays",
                    startDate=_thursday(2).isoformat(),
                    endDate=(_thursday(2) + timedelta(hours=4)).isoformat(),
                    styles=["salsa"], source="beatrice-calendar", url="https://example.com/b")
    assert es._series_signals_conflict(bachata, salsa) is True
    assert dedup_confidence(bachata, salsa) == "review"
    assert dedup_confidence(salsa, bachata) == "review"


def test_same_series_from_two_calendars_is_still_certain():
    start = _thursday(1, hour=20)
    a = _havana(id="unabulla-timba", name="The Timba Messengers at Wally's",
                startDate=start.isoformat(), endDate=(start + timedelta(hours=3)).isoformat(),
                location="Wally's Cafe Jazz Club, 427 Massachusetts Ave, Boston, MA",
                lat=42.3410, lng=-71.0817, styles=["salsa", "timba"],
                source="unabulla-cuban-boston", url="https://example.com/una")
    later = _thursday(3, hour=20)
    b = dict(a, id="timba-messengers-thu", startDate=later.isoformat(),
             endDate=(later + timedelta(hours=3)).isoformat(),
             location="Wally's Cafe, 427 Massachusetts Ave, Boston",
             source="timba-messengers", url="https://example.com/timba")
    assert es._series_signals_conflict(a, b) is False
    assert dedup_confidence(a, b) == "certain"


def test_start_times_hours_apart_demote_to_review():
    early = _havana(name="Havana Club Thursdays", styles=["bachata", "salsa"])
    late_start = _thursday(2, hour=23)
    late = _havana(id="havana-late", name="Havana Club Thursdays", styles=["bachata", "salsa"],
                   startDate=(late_start + timedelta(minutes=30)).isoformat(),
                   endDate=(late_start + timedelta(hours=4)).isoformat(),
                   source="beatrice-calendar", url="https://example.com/b")
    assert es._wall_clock_minutes(early) == 21 * 60
    assert es._wall_clock_minutes(late) == 23 * 60 + 30
    assert dedup_confidence(early, late) == "review"


def test_a_style_named_on_one_side_only_does_not_conflict():
    named = _havana(name="Bachata Thursdays at Havana Club")
    plain = _havana(id="havana-plain", name="Thursdays at Havana Club",
                    startDate=_thursday(2).isoformat(),
                    endDate=(_thursday(2) + timedelta(hours=4)).isoformat(),
                    source="beatrice-calendar", url="https://example.com/b")
    assert es._series_signals_conflict(named, plain) is False
    assert dedup_confidence(named, plain) == "certain"


def test_superset_of_styles_is_the_same_night():
    both = _havana(name="Havana Club Salsa & Bachata Thursdays", styles=["salsa", "bachata"])
    one = _havana(id="havana-one", name="Havana Club Bachata Thursdays",
                  startDate=_thursday(2).isoformat(),
                  endDate=(_thursday(2) + timedelta(hours=4)).isoformat(),
                  source="beatrice-calendar", url="https://example.com/b")
    assert es._series_signals_conflict(both, one) is False


# ── 6. occurrences are compared as instants, not strings ─────────────

def _iso_utc(local: datetime) -> str:
    return local.astimezone(ZoneInfo("UTC")).isoformat()


def test_last_occurrence_is_the_latest_instant_not_the_largest_string():
    late = datetime(2026, 9, 9, 21, 0, tzinfo=NY)           # 9 PM EDT
    early = datetime(2026, 9, 9, 20, 30, tzinfo=NY)         # 8:30 PM EDT
    ev = {"startDate": early.isoformat(),
          # "2026-09-10T00:30:00+00:00" sorts *after* "2026-09-09T21:00:00-04:00"
          # as a string but is the earlier moment.
          "recurrences": [late.isoformat(), _iso_utc(early)]}
    assert _iso_utc(early) > late.isoformat()               # the trap
    assert last_occurrence(ev) == late


def test_occurrence_instants_dedupe_the_same_moment_in_two_spellings():
    d = datetime(2026, 9, 9, 21, 0, tzinfo=NY)
    ev = {"startDate": d.isoformat(), "recurrences": [_iso_utc(d), d.isoformat()]}
    assert es._occurrence_instants(ev) == [d]


def test_collapse_emits_one_eastern_spelling_per_instant():
    d1 = datetime(2026, 9, 9, 21, 0, tzinfo=NY)
    d2 = d1 + timedelta(weeks=1)
    base = {"name": "Kizomba Wednesdays", "location": "1 Pier Rd, Boston", "lat": 42.36,
            "lng": -71.05, "recurring": True, "dayOfWeek": "Wednesday", "source": "manual"}
    a = dict(base, id="a", startDate=d1.isoformat(), endDate=(d1 + timedelta(hours=3)).isoformat(),
             recurrences=[d1.isoformat(), _iso_utc(d2)])
    b = dict(base, id="b", startDate=_iso_utc(d1), endDate=_iso_utc(d1 + timedelta(hours=3)),
             recurrences=[_iso_utc(d1), d2.isoformat()], source="lister-events")
    out = collapse_recurring_series([a, b])
    assert len(out) == 1
    assert out[0]["recurrences"] == [d1.isoformat(), d2.isoformat()]
    assert all(r.endswith("-04:00") for r in out[0]["recurrences"])
    assert datetime.fromisoformat(out[0]["endDate"]) - datetime.fromisoformat(out[0]["startDate"]) \
        == timedelta(hours=3)


def test_naive_timestamps_are_read_as_boston_time():
    ev = {"startDate": "2026-09-09T21:00:00"}
    assert last_occurrence(ev) == datetime(2026, 9, 9, 21, 0, tzinfo=NY)
    assert es._eastern_iso(datetime(2026, 9, 10, 1, 0, tzinfo=ZoneInfo("UTC"))) == \
        "2026-09-09T21:00:00-04:00"


# ── 16. one definition each ───────────────────────────────────────────

def test_weekday_helper_exists_once():
    assert not hasattr(es, "_get_day_of_week")
    assert es._event_day_of_week({"startDate": "2026-09-10T01:00:00+00:00"}) == "Wednesday"
    assert es._event_day_of_week({"dayOfWeek": "Friday", "startDate": "2026-09-09T21:00:00-04:00"}) \
        == "Friday"


def test_calendar_constants_are_shared_with_recurrence_utils():
    assert es.DAYS_LIST is recurrence_utils.DAYS_LIST
    assert es.NY_TZ is recurrence_utils.NY_TZ
    assert es.parse_date is recurrence_utils.parse_date
    assert not hasattr(recurrence_utils, "_parse_date")


def test_series_matching_uses_the_shared_word_helpers():
    # Same stopword set as dedup: "at"/"the" carry nothing on either path.
    assert es._names_are_same_series("salsa at the docks social", "salsa docks social")
    # And the same 1-edit fuzzy tolerance for 3+ letter tokens.
    assert es._names_are_same_series("kizz thursday social", "kiz thursday social")
    assert not es._names_are_same_series("bachata night", "kizomba night")


def test_fuzzy_floor_matches_its_comment():
    assert es._FUZZY_MIN_LEN == 3
    assert es._shared_word_count({"kiz", "thursday"}, {"kizz", "thursday"}) == 2
    assert es._shared_word_count({"dj", "night"}, {"da", "night"}) == 1


def test_source_names_come_from_load_sources(monkeypatch):
    monkeypatch.setattr(es, "load_sources", lambda: [{"id": "x", "name": "X Cal"}, {"id": "y"}])
    assert es._load_source_names() == {"x": "X Cal"}
