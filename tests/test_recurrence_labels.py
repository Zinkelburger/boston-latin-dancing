"""recurrence_utils.recurrence_label: the one place a series gets its
human-readable cadence. publish() stamps the result on every event, and the
site no longer computes a fallback, so an unlabelled series shows nothing."""

import pytest

from recurrence_utils import recurrence_label


def _weekly(day_iso_dates):
    return {"recurring": True, "recurrences": day_iso_dates}


def test_not_recurring_has_no_label():
    assert recurrence_label({"recurring": False, "schedule": [{"dayOfWeek": "Friday"}]}) is None


class TestFromSchedule:
    def test_single_day_with_time(self):
        ev = {"recurring": True, "schedule": [{"dayOfWeek": "Friday", "time": "9:00 PM"}]}
        assert recurrence_label(ev) == "Every Friday · 9:00 PM"

    def test_single_day_without_time(self):
        ev = {"recurring": True, "schedule": [{"dayOfWeek": "Friday"}]}
        assert recurrence_label(ev) == "Every Friday"

    @pytest.mark.parametrize("note, expected", [
        ("every other week", "Every other Friday"),
        ("alternating weeks", "Every other Friday"),
        ("1st Friday of the month", "First Friday of each month"),
        ("3rd friday", "Third Friday of each month"),
        ("last Friday of each month", "Fridays monthly"),
    ])
    def test_note_wins_over_time(self, note, expected):
        ev = {"recurring": True,
              "schedule": [{"dayOfWeek": "Friday", "time": "9:00 PM", "note": note}]}
        assert recurrence_label(ev) == expected

    def test_consecutive_days_are_compacted(self):
        ev = {"recurring": True, "schedule": [
            {"dayOfWeek": "Thursday"}, {"dayOfWeek": "Friday"}, {"dayOfWeek": "Saturday"}]}
        assert recurrence_label(ev) == "Thu–Sat"

    def test_split_days_are_listed(self):
        ev = {"recurring": True, "schedule": [{"dayOfWeek": "Monday"}, {"dayOfWeek": "Friday"}]}
        assert recurrence_label(ev) == "Mon, Fri"

    def test_four_or_more_days_point_at_the_schedule(self):
        ev = {"recurring": True, "schedule": [
            {"dayOfWeek": d} for d in ("Monday", "Tuesday", "Thursday", "Friday")]}
        assert recurrence_label(ev) == "Mon–Tue, Thu–Fri · see schedule"

    def test_every_night(self):
        ev = {"recurring": True, "schedule": [
            {"dayOfWeek": d} for d in
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]}
        assert recurrence_label(ev) == "Every night · see schedule"


class TestFromRecurrenceDates:
    def test_weekly(self):
        ev = _weekly(["2026-09-04T21:00:00-04:00", "2026-09-11T21:00:00-04:00",
                      "2026-09-18T21:00:00-04:00"])
        assert recurrence_label(ev) == "Every Friday"

    def test_biweekly(self):
        ev = _weekly(["2026-09-04T21:00:00-04:00", "2026-09-18T21:00:00-04:00",
                      "2026-10-02T21:00:00-04:00"])
        assert recurrence_label(ev) == "Every other Friday"

    def test_second_saturday_monthly(self):
        # 2026-09-12, 2026-10-10, 2026-11-14 are all second Saturdays.
        ev = _weekly(["2026-09-12T21:00:00-04:00", "2026-10-10T21:00:00-04:00",
                      "2026-11-14T21:00:00-05:00"])
        assert recurrence_label(ev) == "Second Saturday of each month"

    def test_last_sunday_monthly(self):
        # 2026-09-27, 2026-10-25, 2026-11-29 are the last Sundays of their
        # months, and they are the 4th, 4th and 5th Sunday respectively: the
        # ordinal drifts but the series is still "last Sunday".
        ev = _weekly(["2026-09-27T18:00:00-04:00", "2026-10-25T18:00:00-04:00",
                      "2026-11-29T18:00:00-05:00"])
        assert recurrence_label(ev) == "Last Sunday of each month"

    def test_fourth_saturday_stays_fourth_when_a_short_month_makes_it_last(self):
        # 2026-09-26 and 2026-11-28 are both the 4th and the last Saturday;
        # 2026-10-24 is the 4th but not the last (October has five). The
        # ordinal is what agrees, so that is the label.
        ev = _weekly(["2026-09-26T21:00:00-04:00", "2026-10-24T21:00:00-04:00",
                      "2026-11-28T21:00:00-05:00"])
        assert recurrence_label(ev) == "Fourth Saturday of each month"

    def test_mixed_weekdays_have_no_label(self):
        ev = _weekly(["2026-09-04T21:00:00-04:00", "2026-09-09T21:00:00-04:00",
                      "2026-09-18T21:00:00-04:00"])
        assert recurrence_label(ev) is None

    def test_irregular_gap_has_no_label(self):
        ev = _weekly(["2026-09-04T21:00:00-04:00", "2026-09-14T21:00:00-04:00",
                      "2026-10-30T21:00:00-04:00"])
        assert recurrence_label(ev) is None

    def test_mixed_utc_offsets_do_not_split_the_weekday(self):
        # The same Friday-night series spelled in UTC and in Eastern time.
        ev = _weekly(["2026-09-05T01:00:00+00:00", "2026-09-11T21:00:00-04:00",
                      "2026-09-19T01:00:00+00:00"])
        assert recurrence_label(ev) == "Every Friday"


class TestFallbacks:
    def test_single_date_falls_back_to_day_of_week(self):
        ev = {"recurring": True, "dayOfWeek": "Thursday",
              "recurrences": ["2026-09-03T20:00:00-04:00"]}
        assert recurrence_label(ev) == "Every Thursday"

    def test_no_dates_falls_back_to_day_of_week(self):
        assert recurrence_label({"recurring": True, "dayOfWeek": "Thursday"}) == "Every Thursday"

    def test_nothing_to_go_on(self):
        assert recurrence_label({"recurring": True}) is None
