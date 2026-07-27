"""Generic-passthrough-field read side (data-pipeline-upload task, docs/11).

Write side lives in vendor_edits.py (update_extra_field(), same atomic-
write/audit-log contract as every other vendor-data edit) — this module is
read-only: turning VendorExtraField rows into what the Master Data grid
(Part C) needs to render each unmapped column with the right widget.
"""
from backend.db.models import Vendor, VendorExtraField

# UI heuristic, not a financial parameter (CLAUDE.md rule 7 is about model
# math — aging buckets, weights, thresholds — not a widget-choice cutoff for
# a generic passthrough column nobody's model ever reads). A plain constant
# is intentional here, not an oversight; confirm the "15" default with
# Sarath if a real unmapped column ever turns out to need a wider dropdown.
EXTRA_FIELD_DROPDOWN_MAX_DISTINCT = 15

WIDGET_TOGGLE = "toggle"
WIDGET_DROPDOWN = "dropdown"
WIDGET_TEXT = "text"


def derive_widget(distinct_values):
    """decision #5: 2 distinct values -> toggle; 3..CAP -> dropdown (always
    with a Finance-facing "+ Add new value" option, added client-side, not
    part of this list); anything else (0, 1, or > CAP distinct) -> plain
    text. `distinct_values` should already exclude None/blank — a vendor
    with nothing entered yet doesn't count as a "value" for shape purposes.
    """
    n = len(distinct_values)
    if n == 2:
        return WIDGET_TOGGLE, sorted(distinct_values)
    if 3 <= n <= EXTRA_FIELD_DROPDOWN_MAX_DISTINCT:
        return WIDGET_DROPDOWN, sorted(distinct_values)
    return WIDGET_TEXT, None


def list_extra_fields(session):
    """One entry per unmapped column, in first-seen order (matches the
    sheet's own column order closely enough for display — exact order is
    re-derived from the live workbook by the grid endpoint itself, this is
    just the per-column value/widget data): column_name, widget, options
    (None for text), and {vendor_id: value}.
    """
    rows = session.query(VendorExtraField).all()
    by_column = {}
    order = []
    for r in rows:
        if r.column_name not in by_column:
            by_column[r.column_name] = {}
            order.append(r.column_name)
        by_column[r.column_name][r.vendor_id] = r.value

    result = []
    for column_name in order:
        values_by_vendor = by_column[column_name]
        distinct = {v for v in values_by_vendor.values() if v is not None and v != ""}
        widget, options = derive_widget(distinct)
        result.append(
            {
                "column_name": column_name,
                "widget": widget,
                "options": options,
                "values_by_vendor_id": values_by_vendor,
            }
        )
    return result


def vendor_erp_codes(session):
    """{vendor_id: erp_code} — small helper the grid endpoint uses to key
    the known-column values by the same vendor_id the extra-field values
    above already use, instead of erp_code, so the frontend joins on one
    consistent key."""
    return {v.id: v.erp_code for v in session.query(Vendor).all()}
