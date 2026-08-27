"""Guards for timezone artifacts in published times.

A wrong hour is as bad as a wrong day and much harder to notice. Three things
can produce one, and each needs a different guard:

  * double conversion pushes a 9 PM social to 1 AM  -> publish() warns
  * a source renders 9 PM as 5 PM, same day         -> cross_check flags it
  * one source only, nothing to compare             -> only the written rule
    in .cursor/rules/verification.md ("Whose clock to trust")

verify_events.py catches none of them: it derives both sides from the same
JSON-LD instant, so they always agree.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cross_check as cc
import event_store as es


# ── double conversion into the dead hours ─────────────────────────────

def _at(iso: str) -> dict:
    return {"id": "e", "name": "Some Social", "startDate": iso}


def test_small_hours_start_is_flagged():
    assert es.implausible_start_hour(_at("2026-08-30T01:00:00-04:00")) == 1
    assert es.implausible_start_hour(_at("2026-08-30T05:00:00-04:00")) == 5


def test_evening_starts_are_not_flagged():
    for hour in ("17:00", "18:30", "20:00", "21:00", "22:00"):
        assert es.implausible_start_hour(_at(f"2026-08-29T{hour}:00-04:00")) is None


def test_midnight_anchor_is_not_flagged():
    """Fiesta Dance Co lists dates with no time; midnight is deliberate."""
    assert es.implausible_start_hour(_at("2026-08-28T00:00:00-04:00")) is None


def test_utc_input_is_judged_in_boston_time():
    """00:30Z is 8:30 PM here — plausible — not a 12:30 AM start."""
    assert es.implausible_start_hour(_at("2026-08-30T00:30:00+00:00")) is None


def test_missing_or_unparseable_date_is_not_flagged():
    assert es.implausible_start_hour({"startDate": ""}) is None
    assert es.implausible_start_hour({"startDate": "next Friday"}) is None


# ── a source shifted by a whole offset, same calendar day ─────────────

def test_same_day_offset_shift_is_caught():
    """9 PM rendered as 5 PM keeps the day, so the day check calls it agreement."""
    assert cc.whole_offset_shift(
        "2026-08-29T17:00:00-04:00", "2026-08-29T21:00:00-04:00") == 4


def test_winter_offset_is_caught():
    assert cc.whole_offset_shift(
        "2026-01-10T16:00:00-05:00", "2026-01-10T21:00:00-05:00") == 5


def test_identical_instants_written_differently_are_not_a_shift():
    """00:30Z and 8:30 PM EDT are the same moment — this is the good case."""
    assert cc.whole_offset_shift(
        "2026-08-30T00:30:00+00:00", "2026-08-29T20:30:00-04:00") is None


def test_door_time_differences_are_not_flagged():
    """Sources differ by minutes all the time; only whole offsets are suspect."""
    for other in ("2026-08-29T20:30:00-04:00", "2026-08-29T19:30:00-04:00",
                  "2026-08-29T21:45:00-04:00"):
        assert cc.whole_offset_shift(other, "2026-08-29T20:00:00-04:00") is None


def test_a_genuine_reschedule_is_not_called_a_timezone_bug():
    assert cc.whole_offset_shift(
        "2026-08-30T20:00:00-04:00", "2026-08-29T20:00:00-04:00") is None


def test_missing_instants_are_not_a_shift():
    assert cc.whole_offset_shift(None, "2026-08-29T20:00:00-04:00") is None
    assert cc.whole_offset_shift("2026-08-29T20:00:00-04:00", None) is None
