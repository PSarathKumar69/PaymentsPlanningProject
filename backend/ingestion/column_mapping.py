"""Configurable column-mapping layer for the vendor master Excel.

Per CLAUDE.md's no-hardcoding rule, we don't map by fixed column letters.
Columns are located by header text where the label is reliable, and by
position relative to a reliable neighbor where the label is known to move
or be unreliable — confirmed real cases in data/Vendor's Details.xlsx:

- The closing-balance header changes every re-upload (e.g. "As on 31st
  May'26" today) — docs/07-data-pipeline-and-master-sheet.md.
- Month headers mix plain strings ("Apr-25") and Excel datetime objects,
  and contain at least one confirmed real typo (column O is labeled
  "Nov'26" but is structurally Nov-25, the 8th of 14 consecutive months) —
  so month identity is derived from position, not by parsing the label.

The sheet's two-row header (row 2: merged block labels; row 3: actual
column headers) and data-start row are structural constants of this
sheet's known layout, not "hardcoded business logic".
"""
import re
from dataclasses import dataclass
from datetime import date

from backend.shared.enums import VendorCategory

HEADER_ROW = 3
DATA_START_ROW = 4

# Shared between vendor_edits.py (writes Finance's edits back to these
# columns) and load_excel.py (re-ingestion must read the SAME columns back,
# not recompute a default — CLAUDE.md rule 6). Lives here, not in
# vendor_edits.py, so load_excel.py can import it without a circular import
# (vendor_edits.py already imports EXCEL_PATH from load_excel.py).
FIELD_TO_EXCEL_HEADER = {
    "category": "Category",
    "commitment_months": "Commitment Months",
    "assigned_week": "Assigned Week",
}

CATEGORY_EXCEL_LABEL = {
    VendorCategory.MUST_PAY: "Must Pay",
    VendorCategory.COMMITMENT: "Commitment",
    VendorCategory.NORMAL: "Normal",
}
CATEGORY_FROM_EXCEL_LABEL = {label: category for category, label in CATEGORY_EXCEL_LABEL.items()}

# ponytail: start of the historical month block in this specific sheet.
# Flagged for Sarath — confirm this if a future re-upload shifts the month
# range (this can't be derived from the sheet since month labels aren't
# trustworthy; it's an assumption about this file, not a computed value).
SHEET_START_MONTH = date(2025, 4, 1)

_WEEK_RE = re.compile(r"^W(\d+)$", re.IGNORECASE)
_ASSIGNED_WEEK_ORDER_RE = re.compile(r"^W(\d+)-P(\d+)$", re.IGNORECASE)


def _header_cells(ws):
    return [ws.cell(row=HEADER_ROW, column=c) for c in range(1, ws.max_column + 1)]


def _find_col(header_cells, text):
    for cell in header_cells:
        if cell.value is not None and str(cell.value).strip().lower() == text.lower():
            return cell.column
    raise ValueError(f"Column with header {text!r} not found in row {HEADER_ROW}")


def find_column(ws, header_text):
    """Column index for header_text if it exists, else None — never creates
    it. Shared by vendor_edits.py's _find_or_create_column (which creates a
    missing column) and load_excel.py (which must NOT mutate the workbook
    just to check whether a Finance edit has ever touched this field)."""
    for cell in _header_cells(ws):
        if cell.value is not None and str(cell.value).strip().lower() == header_text.lower():
            return cell.column
    return None


def _find_week_cols(header_cells):
    cols = []
    for cell in header_cells:
        if cell.value is None:
            continue
        m = _WEEK_RE.match(str(cell.value).strip())
        if m:
            cols.append((int(m.group(1)), cell.column))
    return sorted(cols)


def _add_months(start, n):
    year = start.year + (start.month - 1 + n) // 12
    month = (start.month - 1 + n) % 12 + 1
    return date(year, month, 1)


@dataclass
class SheetMap:
    erp_code_col: int
    entity_col: int
    vendor_name_col: int
    opening_balance_col: int
    payable_cols: list  # [(month_date, col), ...] oldest first
    total_payable_col: int
    payment_cols: list  # [(month_date, col), ...] oldest first
    total_payment_col: int
    closing_balance_col: int
    week_cols: list  # [(week_number, col), ...]
    week_total_col: int
    assigned_week_order_col: int
    data_start_row: int = DATA_START_ROW


def build_sheet_map(ws) -> SheetMap:
    headers = _header_cells(ws)

    opening_balance_col = _find_col(headers, "Op. Balance")
    total_payable_col = _find_col(headers, "Total Payable")
    total_payment_col = _find_col(headers, "Total Payment")

    payable_start = opening_balance_col + 1
    payable_n = total_payable_col - payable_start
    payment_start = total_payable_col + 1
    payment_n = total_payment_col - payment_start

    if payable_n != payment_n:
        raise ValueError(
            f"Payable block ({payable_n} cols) and Payment block ({payment_n} cols) "
            "differ in length — sheet layout has changed, mapping needs review"
        )

    payable_cols = [(_add_months(SHEET_START_MONTH, i), payable_start + i) for i in range(payable_n)]
    payment_cols = [(_add_months(SHEET_START_MONTH, i), payment_start + i) for i in range(payment_n)]

    # Closing balance sits right after Total Payment — located by position
    # since its header label moves (see module docstring).
    closing_balance_col = total_payment_col + 1

    week_cols = _find_week_cols(headers)
    if not week_cols:
        raise ValueError("No W<n> week columns found")
    week_total_col = max(c for _, c in week_cols) + 1
    assigned_week_order_col = week_total_col + 1

    return SheetMap(
        erp_code_col=_find_col(headers, "ERP Code"),
        entity_col=_find_col(headers, "Entity"),
        vendor_name_col=_find_col(headers, "Vendor Name"),
        opening_balance_col=opening_balance_col,
        payable_cols=payable_cols,
        total_payable_col=total_payable_col,
        payment_cols=payment_cols,
        total_payment_col=total_payment_col,
        closing_balance_col=closing_balance_col,
        week_cols=week_cols,
        week_total_col=week_total_col,
        assigned_week_order_col=assigned_week_order_col,
    )


def parse_assigned_week_order(raw):
    """Split a combined 'W2-P3' cell into (assigned_week, within_week_order).

    0 means the vendor wasn't part of this cycle's plan at all -> (None, None).
    """
    if raw is None:
        return None, None
    text = str(raw).strip()
    if text in ("0", ""):
        return None, None
    m = _ASSIGNED_WEEK_ORDER_RE.match(text)
    if not m:
        raise ValueError(f"Unrecognized assigned-week/order value: {raw!r}")
    return int(m.group(1)), int(m.group(2))


def to_number(raw):
    """Coerce a cell value to a float, treating blanks/whitespace/None as 0.

    Real data-entry artifact confirmed in the source sheet: a handful of
    monthly payment cells contain a stray ' ' string instead of a number.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return 0.0
        return float(raw)
    return float(raw)
