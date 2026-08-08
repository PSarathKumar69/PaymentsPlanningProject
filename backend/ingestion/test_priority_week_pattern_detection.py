"""detect_priority_and_weekly_columns()/detect_column_by_alias_or_pattern()
(column_mapping.py) — the deterministic priority_tag/assigned_week fallback
added after a confirmed real bug: Gemini sometimes rates a genuinely correct
guess "low" confidence on an ambiguous single-word header like "Week", and
ai_column_mapper.py discards low-confidence answers for a still-missing
optional field rather than guess. These two fields are found by header-name
alias or by their own VALUE shape instead, before the AI is even asked.
Confirmed distinction (Sarath): a vendor's own priority tag is always bare
"P<n>"; a weekly column is "W<n>", a bare week number, or "W<n>-P<n>" (the
trailing P<n> there is a same-week pay ORDER, never the vendor's priority
tag, despite reusing the letter "P")."""
import openpyxl

from backend.ingestion.column_mapping import detect_priority_and_weekly_columns


def _sheet(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    return ws


def test_header_alias_finds_weekly_column_even_with_no_priority_column():
    ws = _sheet(
        ["ERP Code", "Vendor Name", "Some Random Col", "Week"],
        [
            ("V1", "Alpha", "x", "W1"),
            ("V2", "Beta", "y", "W2-P3"),
            ("V3", "Gamma", "z", "W1-P1"),
            ("V4", "Delta", "w", "3"),
            ("V5", "Epsi", "v", "W5"),
        ],
    )
    found = detect_priority_and_weekly_columns(ws, header_row=1, data_start_row=2)
    assert found == {"assigned_week": 4}


def test_value_shape_alone_distinguishes_priority_tag_from_weekly_with_no_header_hints():
    ws = _sheet(
        ["ERP Code", "Vendor Name", "Mystery1", "Mystery2"],
        [
            ("V1", "Alpha", "P0", "W1"),
            ("V2", "Beta", "P1", "W2-P3"),
            ("V3", "Gamma", "P2", "W1-P1"),
            ("V4", "Delta", "P3", "3"),
            ("V5", "Epsi", "P5", "W5"),
        ],
    )
    found = detect_priority_and_weekly_columns(ws, header_row=1, data_start_row=2)
    assert found == {"priority_tag": 3, "assigned_week": 4}


def test_neither_field_detected_when_values_dont_match_either_shape():
    ws = _sheet(
        ["ERP Code", "Vendor Name", "Notes"],
        [
            ("V1", "Alpha", "call vendor"),
            ("V2", "Beta", "on hold"),
            ("V3", "Gamma", "resolved"),
        ],
    )
    found = detect_priority_and_weekly_columns(ws, header_row=1, data_start_row=2)
    assert found == {}


def test_exclude_cols_prevents_a_claimed_column_from_being_redetected():
    ws = _sheet(
        ["ERP Code", "Vendor Name", "Week"],
        [("V1", "Alpha", "W1"), ("V2", "Beta", "W2")],
    )
    found = detect_priority_and_weekly_columns(ws, header_row=1, data_start_row=2, exclude_cols={3})
    assert found == {}


def test_real_confirmed_typos_prioity_and_weeek_still_found_by_fuzzy_header_match():
    ws = _sheet(
        ["ERP Code", "Vendor Name", "Prioity", "Weeek"],
        [
            ("V1", "Alpha", "P0", "W1"),
            ("V2", "Beta", "P1", "W2-P3"),
            ("V3", "Gamma", "P2", "W1-P1"),
            ("V4", "Delta", "P3", "3"),
            ("V5", "Epsi", "P5", "W5"),
        ],
    )
    found = detect_priority_and_weekly_columns(ws, header_row=1, data_start_row=2)
    assert found == {"priority_tag": 3, "assigned_week": 4}


def test_fuzzy_header_match_alone_is_not_enough_without_matching_values():
    """A typo'd header ("Prioity") next to a column that genuinely holds
    something else (free-text notes, not P<n> values) must not be claimed —
    the fuzzy match is only trusted when the column's own values back it up."""
    ws = _sheet(
        ["ERP Code", "Vendor Name", "Prioity"],
        [("V1", "Alpha", "called twice"), ("V2", "Beta", "no response")],
    )
    found = detect_priority_and_weekly_columns(ws, header_row=1, data_start_row=2)
    assert found == {}
