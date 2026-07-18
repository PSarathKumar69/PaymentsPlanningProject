"""Unit tests for calendar_utils — no DB needed, pure calendar math."""
from datetime import date

from backend.weekly_planning.calendar_utils import week_for_date, weeks_in_month


def test_week_for_date_7_day_chunks_within_a_31_day_month():
    assert week_for_date(date(2026, 1, 1)) == 1
    assert week_for_date(date(2026, 1, 7)) == 1
    assert week_for_date(date(2026, 1, 8)) == 2
    assert week_for_date(date(2026, 1, 14)) == 2
    assert week_for_date(date(2026, 1, 15)) == 3
    assert week_for_date(date(2026, 1, 21)) == 3
    assert week_for_date(date(2026, 1, 22)) == 4
    assert week_for_date(date(2026, 1, 28)) == 4
    # Last week absorbs the remainder (29-31), not overflowing into a 6th week.
    assert week_for_date(date(2026, 1, 29)) == 5
    assert week_for_date(date(2026, 1, 31)) == 5


def test_week_for_date_non_leap_february_has_only_4_weeks():
    assert weeks_in_month(2026, 2) == 4
    assert week_for_date(date(2026, 2, 22)) == 4
    assert week_for_date(date(2026, 2, 28)) == 4  # last day of month, still week 4 not week 5


def test_week_for_date_month_boundary_resets_to_week_1():
    # Last day of a 31-day month is week 5; the very next calendar day, being
    # day 1 of the next month, resets to week 1 regardless of what the prior
    # month's week count was.
    assert week_for_date(date(2026, 1, 31)) == 5
    assert week_for_date(date(2026, 2, 1)) == 1


def test_week_for_date_30_day_month():
    assert weeks_in_month(2026, 4) == 5
    assert week_for_date(date(2026, 4, 30)) == 5  # week 5 only has days 29-30
