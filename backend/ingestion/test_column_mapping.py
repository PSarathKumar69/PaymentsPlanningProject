"""Unit tests for column_mapping.py's generic header-display helper (pure
logic, no DB/Excel I/O) — see test_header_row_detection.py/
test_sheet_start_month.py for this module's other, feature-specific tests.
"""
from datetime import date, datetime

from backend.ingestion.column_mapping import format_header_value


def test_format_header_value_formats_datetime_cell_as_month_year():
    # Master Grid bug: a literal datetime-typed header cell (confirmed real
    # case — some payable/payment month headers in the real sheet) must not
    # render as str()'s raw "2026-04-26 00:00:00".
    assert format_header_value(datetime(2026, 4, 26, 0, 0)) == "Apr-26"


def test_format_header_value_formats_date_cell_as_month_year():
    assert format_header_value(date(2026, 4, 26)) == "Apr-26"


def test_format_header_value_passes_plain_string_through_unchanged():
    assert format_header_value("Apr-25") == "Apr-25"
    assert format_header_value("  ERP Code  ") == "ERP Code"


def test_format_header_value_none_stays_none():
    assert format_header_value(None) is None
