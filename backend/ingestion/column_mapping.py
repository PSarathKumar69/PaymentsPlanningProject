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

from backend.shared.enums import VendorCategory, VendorPriorityTag

HEADER_ROW = 3
DATA_START_ROW = 4

# Shared between vendor_edits.py (writes Finance's edits back to these
# columns) and load_excel.py (re-ingestion must read the SAME columns back,
# not recompute a default — CLAUDE.md rule 6). Lives here, not in
# vendor_edits.py, so load_excel.py can import it without a circular import
# (vendor_edits.py already imports EXCEL_PATH from load_excel.py).
#
# priority_tag (AI-mapping task's Part A prerequisite): mirrors
# category/commitment_months/assigned_week exactly — a dedicated column,
# found by header text, safe-defaults to None (unchanged/unset) when the
# column or a value is missing, never crashes the upload. Sheet values are
# expected to already be the literal enum labels (P0-P5, confirmed in
# real test sheets), so no separate label-translation dict is needed the
# way CATEGORY_EXCEL_LABEL is for category below.
FIELD_TO_EXCEL_HEADER = {
    "category": "Category",
    "commitment_months": "Commitment Months",
    "assigned_week": "Assigned Week",
    "priority_tag": "Priority Tag",
}

CATEGORY_EXCEL_LABEL = {
    VendorCategory.MUST_PAY: "Must Pay",
    VendorCategory.COMMITMENT: "Commitment",
    VendorCategory.NORMAL: "Normal",
    VendorCategory.INACTIVE: "Inactive",
}
CATEGORY_FROM_EXCEL_LABEL = {label: category for category, label in CATEGORY_EXCEL_LABEL.items()}

# REQUIRED_HEADER_DEFAULTS: the sheet's structural, single-header-lookup
# fields — the ones build_sheet_map() has always fail-fast raised on if
# missing (never had a safe default, unlike category/commitment_months/
# assigned_week/priority_tag above). These plus FIELD_TO_EXCEL_HEADER
# together are every logical field the AI-mapping task's gap-filler can
# possibly resolve — anything positional/regex-derived (Closing Balance,
# the monthly Payable/Payment blocks, W1-W5) has no single fixed header to
# search for and is deliberately out of scope for that mechanism.
REQUIRED_HEADER_DEFAULTS = {
    "erp_code": "ERP Code",
    "entity": "Entity",
    "vendor_name": "Vendor Name",
    "opening_balance": "Op. Balance",
    "total_payable": "Total Payable",
    "total_payment": "Total Payment",
}
REQUIRED_FIELDS = frozenset(REQUIRED_HEADER_DEFAULTS)

ALL_MAPPABLE_FIELDS = {**REQUIRED_HEADER_DEFAULTS, **FIELD_TO_EXCEL_HEADER}

# One-line, finance-analyst-readable description per field — fed verbatim
# into the AI column-mapper's prompt (ai_column_mapper.py) so it gets the
# same context a human would need, not just a bare field name.
FIELD_DESCRIPTIONS = {
    "erp_code": "Unique vendor/ERP identifier code — one row per vendor, values are short alphanumeric codes.",
    "entity": "The legal entity/business unit this vendor's bills are booked under.",
    "vendor_name": "The vendor's business/company name.",
    "opening_balance": "The vendor's outstanding balance carried forward, BEFORE this sheet's monthly Payable/Payment columns are applied.",
    "total_payable": "The sum total of every monthly Payable column for this vendor.",
    "total_payment": "The sum total of every monthly Payment column for this vendor.",
    "category": "Finance's vendor category tag — one of exactly: Must Pay, Commitment, Normal, Inactive.",
    "commitment_months": "For Commitment-category vendors only: an integer, the number of months their balance amortizes over.",
    "assigned_week": "Which week (an integer, typically 1-5) of the current payment cycle this vendor is assigned to.",
    "priority_tag": "Finance's assigned urgency tag for this vendor — one of exactly: P0, P1, P2, P3, P4, P5.",
}

PRIORITY_TAG_VALUES = {tag.value for tag in VendorPriorityTag}

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


class MissingRequiredColumnsError(ValueError):
    """Raised by build_sheet_map() when one of REQUIRED_HEADER_DEFAULTS'
    headers can't be found under either a persisted override or its fixed
    default text. `missing_fields` names every one that failed (all of
    them, not just the first — the AI-mapping gap-filler needs the full
    list in one shot, not one Gemini call per field)."""

    def __init__(self, missing_fields):
        self.missing_fields = list(missing_fields)
        super().__init__(
            f"Required column(s) not found in the sheet: {', '.join(self.missing_fields)}"
        )


def resolve_header(field, header_overrides=None):
    """The header text to search for `field` — a persisted AI/config
    override if one exists for it, else the fixed default. Pure text
    lookup, never touches the workbook."""
    default = ALL_MAPPABLE_FIELDS[field]
    if header_overrides and field in header_overrides:
        return header_overrides[field]
    return default


def missing_mappable_fields(ws, fields, header_overrides=None):
    """Non-raising pre-flight check: which of `fields` (a subset of
    ALL_MAPPABLE_FIELDS) can't currently be resolved on this sheet, given
    `header_overrides`. Used by the AI-mapping gap-filler to discover every
    unresolved field before calling build_sheet_map() (which still fails
    fast on the first missing required field, same as it always has)."""
    return [f for f in fields if find_column(ws, resolve_header(f, header_overrides)) is None]


def build_sheet_map(ws, header_overrides=None) -> SheetMap:
    """header_overrides: optional {logical_field: header_text}, sourced from
    column_mapping_store.py's persisted AI-mapping table — consulted before
    falling back to each required field's fixed default header text. None
    (the default) reproduces the exact behavior this function has always
    had."""
    headers = _header_cells(ws)

    try:
        opening_balance_col = _find_col(headers, resolve_header("opening_balance", header_overrides))
        total_payable_col = _find_col(headers, resolve_header("total_payable", header_overrides))
        total_payment_col = _find_col(headers, resolve_header("total_payment", header_overrides))
    except ValueError:
        raise MissingRequiredColumnsError(
            missing_mappable_fields(ws, REQUIRED_FIELDS, header_overrides)
        ) from None

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

    try:
        erp_code_col = _find_col(headers, resolve_header("erp_code", header_overrides))
        entity_col = _find_col(headers, resolve_header("entity", header_overrides))
        vendor_name_col = _find_col(headers, resolve_header("vendor_name", header_overrides))
    except ValueError:
        raise MissingRequiredColumnsError(
            missing_mappable_fields(ws, REQUIRED_FIELDS, header_overrides)
        ) from None

    return SheetMap(
        erp_code_col=erp_code_col,
        entity_col=entity_col,
        vendor_name_col=vendor_name_col,
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


def known_column_indices(sheet_map, extra_cols=()):
    """Every column index build_sheet_map()/load() already understands.
    `extra_cols`: the dedicated Finance-edit columns (Category, Commitment
    Months, Assigned Week) — found via find_column() by the caller (they may
    not exist yet on a sheet nobody's edited through the UI yet), passed in
    rather than re-found here to avoid a second lookup. Feeds
    unmapped_header_columns() below — the data-pipeline-upload task's
    generic-passthrough-field detection (docs/11's "store and show it, never
    feed it into any model" rule)."""
    known = {
        sheet_map.erp_code_col,
        sheet_map.entity_col,
        sheet_map.vendor_name_col,
        sheet_map.opening_balance_col,
        sheet_map.total_payable_col,
        sheet_map.total_payment_col,
        sheet_map.closing_balance_col,
        sheet_map.week_total_col,
        sheet_map.assigned_week_order_col,
    }
    known.update(c for _, c in sheet_map.payable_cols)
    known.update(c for _, c in sheet_map.payment_cols)
    known.update(c for _, c in sheet_map.week_cols)
    known.update(c for c in extra_cols if c is not None)
    return known


def unmapped_header_columns(ws, sheet_map, extra_cols=()):
    """[(col_index, header_text), ...] for every header cell NOT already
    understood by the mapping above, in column order — these become
    VendorExtraField generic-passthrough columns (load_excel.py), never fed
    into any model (CLAUDE.md rule 3). Skips blank headers (trailing empty
    columns past the sheet's real content)."""
    known = known_column_indices(sheet_map, extra_cols)
    return [
        (cell.column, str(cell.value).strip())
        for cell in _header_cells(ws)
        if cell.column not in known and cell.value is not None and str(cell.value).strip() != ""
    ]


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
