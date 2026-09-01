"""fetch_submissions: recurring submissions keep their cadence, dateless ones
are reported instead of invented, and a missing admin token is a failure.

Geocoding is stubbed so make_event never touches the network.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_submissions as fs  # noqa: E402
import scraper_utils  # noqa: E402
from recurrence_utils import recurrence_label  # noqa: E402

TODAY = datetime(2026, 9, 1)  # a Tuesday


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(scraper_utils, "geocode", lambda location: (42.36, -71.06))


def _sub(**overrides):
    base = {
        "email": "org@example.com",
        "instagram": "",
        "event_name": "Bachata Nights",
        "event_url": "https://example.com/bachata",
        "styles": ["bachata"],
        "location": "1 Main St, Cambridge, MA",
        "is_recurring": False,
        "date": "",
        "time": "",
        "recurrence_type": "",
        "day_of_week": "",
        "week_of_month": "",
        "start_date": "",
        "notes": "",
        "submitted_at": "2026-08-30T12:00:00+00:00",
    }
    base.update(overrides)
    return base


# ── Recurring mapping ─────────────────────────────────────────────────


def test_monthly_submission_carries_recurrence_into_event():
    ev = fs.submission_to_event(_sub(
        is_recurring=True, recurrence_type="monthly",
        day_of_week="Saturday", week_of_month="2nd",
    ), today=TODAY)

    assert ev["recurring"] is True
    assert ev["dayOfWeek"] == "Saturday"
    assert ev["recurrenceLabel"] == "Second Saturday of each month"
    # 2nd Saturdays inside the 12-week window from 2026-09-01.
    assert ev["recurrences"] == [
        "2026-09-12T20:00:00-04:00",
        "2026-10-10T20:00:00-04:00",
        "2026-11-14T20:00:00-05:00",
    ]
    assert ev["startDate"] == ev["recurrences"][0]
    assert ev["endDate"] == "2026-09-12T23:00:00-04:00"
    # The recurrence engine reads the same cadence back off the dates.
    assert recurrence_label(ev) == "Second Saturday of each month"


def test_last_weekday_of_month():
    ev = fs.submission_to_event(_sub(
        is_recurring=True, recurrence_type="monthly",
        day_of_week="Friday", week_of_month="Last",
    ), today=TODAY)
    assert ev["recurrences"][:2] == ["2026-09-25T20:00:00-04:00", "2026-10-30T20:00:00-04:00"]
    assert ev["recurrenceLabel"] == "Last Friday of each month"


def test_weekly_submission_with_time():
    ev = fs.submission_to_event(_sub(
        is_recurring=True, recurrence_type="weekly", day_of_week="Thursday", time="9:30 PM",
    ), today=TODAY)
    assert ev["recurrenceLabel"] == "Every Thursday"
    assert ev["recurrences"][:3] == [
        "2026-09-03T21:30:00-04:00",
        "2026-09-10T21:30:00-04:00",
        "2026-09-17T21:30:00-04:00",
    ]
    assert len(ev["recurrences"]) == 12
    assert recurrence_label(ev) == "Every Thursday"


def test_biweekly_anchors_on_submitted_start_date():
    ev = fs.submission_to_event(_sub(
        is_recurring=True, recurrence_type="biweekly",
        day_of_week="Thursday", start_date="2026-09-10",
    ), today=TODAY)
    assert ev["recurrences"][:3] == [
        "2026-09-10T20:00:00-04:00",
        "2026-09-24T20:00:00-04:00",
        "2026-10-08T20:00:00-04:00",
    ]
    assert ev["recurrenceLabel"] == "Every other Thursday"
    assert recurrence_label(ev) == "Every other Thursday"


def test_biweekly_without_start_date_uses_shared_parity():
    dates = fs.recurrence_dates("biweekly", "Friday", today=TODAY)
    # Parity anchored on 2026-01-02 (a Friday), the same reference the
    # store and frontend use for "every other".
    assert dates[0] == datetime(2026, 9, 11)
    assert (dates[0] - fs.EVERY_OTHER_REF).days % 14 == 0


def test_recurring_series_id_is_stable_as_dates_roll_forward():
    sub = _sub(is_recurring=True, recurrence_type="weekly", day_of_week="Sunday")
    first = fs.submission_to_event(sub, today=TODAY)
    later = fs.submission_to_event(sub, today=datetime(2026, 10, 15))
    assert first["id"] == later["id"]
    assert first["startDate"] != later["startDate"]
    assert later["startDate"] == "2026-10-18T20:00:00-04:00"


def test_series_never_starts_before_submitted_start_date():
    ev = fs.submission_to_event(_sub(
        is_recurring=True, recurrence_type="weekly",
        day_of_week="Monday", start_date="2026-10-05",
    ), today=TODAY)
    assert ev["recurrences"][0] == "2026-10-05T20:00:00-04:00"


# ── One-off and dateless paths ────────────────────────────────────────


def test_one_off_submission_unchanged():
    ev = fs.submission_to_event(_sub(date="2026-09-19", time="8:00 PM"), today=TODAY)
    assert ev["startDate"] == "2026-09-19T20:00:00-04:00"
    assert ev["recurring"] is False
    assert "recurrences" not in ev
    assert ev["source"] == "submissions"
    assert ev["id"].startswith("submit-")


def test_dateless_submission_is_not_given_todays_date():
    assert fs.submission_to_event(_sub(), today=TODAY) is None
    assert fs.submission_to_event(_sub(date="next friday"), today=TODAY) is None
    # Recurring flag without the fields the form collects is equally undated.
    assert fs.submission_to_event(_sub(is_recurring=True), today=TODAY) is None


def test_convert_reports_needs_date_entries():
    events, needs_date = fs.convert_submissions(
        [_sub(date="2026-09-19"), _sub(event_name="Mystery Social", instagram="@mystery", email="")],
        today=TODAY,
    )
    assert len(events) == 1
    assert needs_date == [{
        "event_name": "Mystery Social",
        "event_url": "https://example.com/bachata",
        "contact": "@mystery",
        "submitted_at": "2026-08-30T12:00:00+00:00",
        "needs_date": True,
        "reason": "no date given",
    }]


def test_main_warns_on_stderr_and_writes_only_dated_events(monkeypatch, capsys):
    monkeypatch.setattr(fs, "ADMIN_TOKEN", "tok")
    monkeypatch.setattr(fs, "fetch_submissions", lambda: [_sub(date="2026-09-19"), _sub(event_name="Undated")])
    written = {}
    monkeypatch.setattr(fs, "write_scraped", lambda sid, events: written.setdefault(sid, events))

    summary = fs.main()

    assert [e["name"] for e in written["submissions"]] == ["Bachata Nights"]
    assert summary["fetched"] == 2 and summary["converted"] == 1
    assert summary["needs_date"][0]["event_name"] == "Undated"
    assert "Undated" in capsys.readouterr().err


# ── Failure semantics ─────────────────────────────────────────────────


def test_missing_admin_token_exits_nonzero_without_writing(monkeypatch, capsys):
    monkeypatch.setattr(fs, "ADMIN_TOKEN", "")
    calls = []
    monkeypatch.setattr(fs, "write_scraped", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(fs.requests, "get", lambda *a, **k: pytest.fail("must not call the API"))

    with pytest.raises(SystemExit) as exc:
        fs.main()

    assert exc.value.code == 2
    assert calls == []
    assert "BLD_ADMIN_TOKEN" in capsys.readouterr().err


def test_fetch_raises_when_token_missing(monkeypatch):
    monkeypatch.setattr(fs, "ADMIN_TOKEN", "")
    with pytest.raises(fs.MissingAdminToken):
        fs.fetch_submissions()
