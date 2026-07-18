"""Calendar utility — how many weeks (W1-W5) a given month actually has.

Per CLAUDE.md's no-hardcoding rule: the week count must be derived from the
calendar, not assumed to always be 5. For validation/display only — it does
not affect the assigned-week grouping logic in planner.py.
"""
import calendar
import math


def weeks_in_month(year, month):
    """W1-W5 = consecutive 7-day chunks of the month starting on day 1.
    Every month has 5 except a non-leap February (28 days -> 4)."""
    _, days_in_month = calendar.monthrange(year, month)
    return math.ceil(days_in_month / 7)


def week_for_date(d):
    """Which W1-W5 week of its own month `d` falls into.

    DEFAULT — confirmed convention, matching weeks_in_month's own 7-day-chunk
    docstring rather than inventing a conflicting one: day 1-7 -> week 1,
    8-14 -> week 2, ..., with the real last week absorbing whatever remainder
    days a 29/30/31-day month has left over (e.g. a 31-day month's week 5 is
    only days 29-31). Reuses weeks_in_month so the two can never drift apart;
    the min() is a defensive clamp only, never actually triggered since a
    valid date's day-of-month can't exceed its own month's day count.
    """
    return min(math.ceil(d.day / 7), weeks_in_month(d.year, d.month))
