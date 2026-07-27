"""Pure-function tests for widget derivation (decision #5) — plain values in,
no DB/session needed. The DB-backed round-trip (real upload -> widget
derivation -> edit -> Excel write-back) lives in
backend/ingestion/test_upload.py::test_extra_field_round_trip_widget_derivation_and_edit,
since that needs a real ingested sheet to be meaningful.
"""
import pytest

from backend.configuration.extra_fields import (
    EXTRA_FIELD_DROPDOWN_MAX_DISTINCT,
    WIDGET_DROPDOWN,
    WIDGET_TEXT,
    WIDGET_TOGGLE,
    derive_widget,
)


def test_two_distinct_values_is_a_toggle():
    widget, options = derive_widget({"Yes", "No"})
    assert widget == WIDGET_TOGGLE
    assert options == ["No", "Yes"]


def test_three_to_cap_distinct_values_is_a_dropdown():
    widget, options = derive_widget({"A", "B", "C"})
    assert widget == WIDGET_DROPDOWN
    assert options == ["A", "B", "C"]


def test_exactly_at_the_cap_is_still_a_dropdown():
    values = {f"v{i}" for i in range(EXTRA_FIELD_DROPDOWN_MAX_DISTINCT)}
    widget, options = derive_widget(values)
    assert widget == WIDGET_DROPDOWN
    assert len(options) == EXTRA_FIELD_DROPDOWN_MAX_DISTINCT


def test_one_over_the_cap_is_plain_text():
    values = {f"v{i}" for i in range(EXTRA_FIELD_DROPDOWN_MAX_DISTINCT + 1)}
    widget, options = derive_widget(values)
    assert widget == WIDGET_TEXT
    assert options is None


def test_zero_or_one_distinct_value_is_plain_text():
    """Not explicitly specified by decision #5 (which starts at "2 distinct
    -> toggle") — a column where every vendor has the same value, or none at
    all, has nothing meaningful to toggle between, so this falls to plain
    text rather than a toggle with one option. Flagged default, not a
    silent guess (house style, see allocator.py's FALLBACK_BUCKET)."""
    widget, options = derive_widget(set())
    assert widget == WIDGET_TEXT
    widget, options = derive_widget({"only-value"})
    assert widget == WIDGET_TEXT


if __name__ == "__main__":
    test_two_distinct_values_is_a_toggle()
    test_three_to_cap_distinct_values_is_a_dropdown()
    test_exactly_at_the_cap_is_still_a_dropdown()
    test_one_over_the_cap_is_plain_text()
    test_zero_or_one_distinct_value_is_plain_text()
    print("extra_fields self-checks passed")
