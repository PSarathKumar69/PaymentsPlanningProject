"""Vendor field edits — Configuration module, vendor-data half only
(docs/11-configuration-module.md). System-config editing (Model 1 weights,
Model 3 thresholds) and replacement-Excel-upload diffing are separate,
later work — not in scope here.

Every edit spans two systems: the DB and the real Excel file — but only
when this call *owns* its own session (no session= passed in) does it
touch Excel at all. Session ownership is the deciding line:

- Owns the session (the normal call, no session= argument): stage the
  Excel edit to a temp file, commit the DB, and only then publish the
  staged file (atomic os.replace) — narrowing the drift window to "DB
  committed, but the Excel replace hasn't happened or failed yet," rather
  than the older "Excel already saved, DB commit then fails" ordering. On
  any failure before a successful publish: roll back, never publish.
- Caller supplies the session (composing into a larger transaction): this
  function is DB-only. It flushes the vendor-field change and audit_log
  row into the shared session and returns — it never commits, never rolls
  back, and never touches the real Excel file. It has no way to know
  whether or when the caller's transaction will actually commit, and an
  Excel publish can't be undone the way a DB row can, so both are left
  entirely to the caller (any exception here propagates untouched, so the
  caller's own rollback covers this function's own pending changes too).

# RESIDUAL RISK — flagged, not fully solved: this is not a real two-phase
# commit across the DB and the filesystem. If the DB commit succeeds but
# the subsequent os.replace() fails or the process dies in that narrow
# window, the DB and Excel will disagree (DB updated, Excel still old)
# with nothing to reconcile them automatically. A full fix would need a
# write-ahead log / reconciliation pass across both systems, which is a
# bigger change than this pass calls for — flagging for Sarath rather than
# building it now. See test_db_commit_failure_after_excel_write_staged for
# what's proven today: a commit failure no longer leaves Excel ahead of
# the DB (the case that was previously untested).
"""
import os
import tempfile
from datetime import datetime

import openpyxl
from sqlalchemy import func

from backend.db.models import AuditLog, MonthlyLedger, Vendor, VendorExtraField
from backend.db.session import SessionLocal
from backend.ingestion.column_mapping import (
    CATEGORY_EXCEL_LABEL,
    FIELD_TO_EXCEL_HEADER,
    HEADER_ROW,
    build_sheet_map,
    find_column,
)
from backend.ingestion.load_excel import EXCEL_PATH
from backend.models.new_model_2.allocator import _seed_and_read_buckets
from backend.shared.enums import ChangeSource, VendorCategory, VendorPriorityTag
from backend.weekly_planning.calendar_utils import weeks_in_month

# DEFAULT — confirm with Sarath: category/commitment_months don't exist as
# columns in the real Excel yet, and assigned_week is currently packed with
# within-week-order into one legacy "W2-P3" column. Rather than re-packing
# edits into that legacy string format, this writes each field to its own
# clean, dedicated column (added to the sheet on first use if missing) and
# leaves the legacy combined column untouched — within_week_order is
# code-computed elsewhere and was never meant to be hand-edited anyway.
# (FIELD_TO_EXCEL_HEADER/CATEGORY_EXCEL_LABEL live in column_mapping.py, not
# here, so load_excel.py can read the same columns back on re-ingestion
# without a circular import — see that module's docstring.)


def _current_max_assigned_week(session):
    """How many week tags (W1-W<n>) are valid right now — derived from the
    calendar via the latest ingested ledger month (CLAUDE.md rule 7: never
    hardcode the week count to 5; some months only have 4). Reuses
    calendar_utils.weeks_in_month, the same function planner.py's own tests
    already exercise for this.
    """
    latest_month = session.query(func.max(MonthlyLedger.month)).scalar()
    if latest_month is None:
        return 5  # no ledger data at all yet (e.g. a fresh/empty DB) — fall back to the max possible
    return weeks_in_month(latest_month.year, latest_month.month)


def _validate(field, new_value, session=None):
    if field not in FIELD_TO_EXCEL_HEADER:
        raise ValueError(f"unknown field {field!r}; must be one of {sorted(FIELD_TO_EXCEL_HEADER)}")

    if field == "category":
        if isinstance(new_value, VendorCategory):
            return new_value
        try:
            return VendorCategory(new_value)
        except ValueError:
            raise ValueError(
                f"invalid category {new_value!r}; must be one of {[c.value for c in VendorCategory]}"
            ) from None

    if field == "commitment_months":
        if isinstance(new_value, bool) or not isinstance(new_value, int) or new_value < 1:
            raise ValueError(f"commitment_months must be a positive integer, got {new_value!r}")
        return new_value

    if field == "priority_tag":
        if new_value is None:
            return None
        tag = new_value.value if isinstance(new_value, VendorPriorityTag) else str(new_value).strip().upper()
        # Bug fix (this task): used to validate against the fixed
        # VendorPriorityTag Python enum (P0-P5 only) — a Configuration-added
        # P6+ bucket (priority_bucket_edits.py::add_bucket) could never
        # actually be assigned to a vendor. Validated live against P0/P1
        # (fixed, auto-derived — never Finance-assignable in practice, but
        # kept permissive here exactly like the old enum was) plus whatever
        # buckets currently exist in the priority_buckets table — the same
        # DB-backed lookup allocator.py itself reads (_seed_and_read_buckets,
        # idempotent: seeds today's P2/P3/P4/P5 defaults on an empty table,
        # otherwise just reads back Configuration's current rows).
        auto_tags = {VendorPriorityTag.P0.value, VendorPriorityTag.P1.value}
        bucket_order, _, _ = _seed_and_read_buckets(session)
        valid_tags = auto_tags | set(bucket_order)
        if tag not in valid_tags:
            raise ValueError(f"invalid priority_tag {new_value!r}; must be one of {sorted(valid_tags)} or None")
        return tag

    # assigned_week
    if new_value is None:
        return None
    max_week = _current_max_assigned_week(session)
    if isinstance(new_value, bool) or not isinstance(new_value, int) or not (1 <= new_value <= max_week):
        raise ValueError(
            f"assigned_week must be an integer 1-{max_week} (this month's actual week count) or None, "
            f"got {new_value!r}"
        )
    return new_value


def _find_or_create_column(ws, header_text):
    col = find_column(ws, header_text)
    if col is not None:
        return col
    new_col = ws.max_column + 1
    ws.cell(row=HEADER_ROW, column=new_col, value=header_text)
    return new_col


def _find_vendor_row(ws, sheet_map, erp_code):
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == erp_code:
            return row
    raise ValueError(f"vendor {erp_code!r} not found in the Excel sheet")


def _excel_cell_value(field, new_value):
    if field == "category":
        return CATEGORY_EXCEL_LABEL[new_value]
    # priority_tag is already a plain string (_validate above never returns
    # an enum instance) — falls through to the generic return below, same
    # as commitment_months/assigned_week.
    return new_value  # commitment_months: int; assigned_week: int or None (clears the cell); priority_tag: str or None


def _clean_value(field, value):
    """Human-readable audit_log text — never a raw Python repr like
    "VendorCategory.NORMAL". Reuses the same Finance-facing label the
    Excel cell gets, so the audit log and the Excel file always agree on
    what a value "looks like" (one shared label per field, not two)."""
    if value is None:
        return None
    if field == "category":
        return CATEGORY_EXCEL_LABEL[value]
    # priority_tag is already a plain string — falls through to str(value)
    # below (a no-op for it), same as commitment_months/assigned_week.
    return str(value)


def _stage_excel_cell_write(header_text, erp_code, cell_value, excel_path):
    """Generic version of the staging step: writes one (header, vendor row)
    cell to a fresh temp file (same directory as excel_path, so the later
    os.replace is same-filesystem and atomic) and returns its path. Nothing
    is published to excel_path yet — see the two callers below. Shared by
    update_vendor_field() (known fields, header resolved from
    FIELD_TO_EXCEL_HEADER) and update_extra_field() (data-pipeline-upload
    task's generic passthrough columns, header is the literal Excel text) —
    one atomic-write implementation, not two."""
    wb = openpyxl.load_workbook(excel_path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)

    col = _find_or_create_column(ws, header_text)
    row = _find_vendor_row(ws, sheet_map, erp_code)
    # openpyxl's ws.cell(..., value=None) is a no-op (None means "getter
    # mode", not "clear") — direct attribute assignment is required to
    # actually blank a cell (needed when assigned_week is cleared to None).
    ws.cell(row=row, column=col).value = cell_value

    directory = os.path.dirname(os.path.abspath(excel_path)) or "."
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=directory)
    os.close(fd)
    wb.save(tmp_path)
    return tmp_path


def _stage_excel_write(erp_code, field, new_value, excel_path):
    """Known-field (category/commitment_months/assigned_week) staging —
    thin wrapper over the generic cell-write above."""
    return _stage_excel_cell_write(FIELD_TO_EXCEL_HEADER[field], erp_code, _excel_cell_value(field, new_value), excel_path)


def update_vendor_field(vendor_id, field, new_value, source=ChangeSource.UI_EDIT, session=None, excel_path=None):
    """Validates, updates the DB, records an audit_log row, and — only if
    this call owns its own session — writes back to the real Excel.

    Owns the session (session=None, the normal call): stage the Excel
    edit, commit the DB, then publish the staged file (os.replace) — in
    that order. A failure anywhere before a successful publish rolls back
    and never publishes (see module docstring for the residual-risk window
    this does and doesn't cover).

    Caller supplies `session`: DB-only. Flushes the change into the shared
    session and returns; never commits, never rolls back, never touches
    Excel — the caller owns that transaction and must handle its own
    commit/rollback (and, for now, its own Excel write-back).

    Returns (old_value, new_value).
    """
    excel_path = excel_path or EXCEL_PATH

    owns_session = session is None
    session = session or SessionLocal()
    tmp_path = None
    try:
        # assigned_week's valid range depends on the calendar (this month's
        # actual week count), so validation needs a session now — moved
        # inside the try so a validation failure is cleaned up the same way
        # as any other failure below (rollback if this call owns the
        # session, always close if it does).
        new_value = _validate(field, new_value, session=session)
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        old_value = getattr(vendor, field)
        setattr(vendor, field, new_value)
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=vendor_id,
                field_name=field,
                old_value=_clean_value(field, old_value),
                new_value=_clean_value(field, new_value),
                source=source.value,
            )
        )
        session.flush()  # surface any DB-side error before touching Excel at all

        if not owns_session:
            # Caller owns the transaction: DB-only, nothing published.
            return old_value, new_value

        tmp_path = _stage_excel_write(vendor.erp_code, field, new_value, excel_path)
        session.commit()  # publish only happens below, after this succeeds
        os.replace(tmp_path, excel_path)
        tmp_path = None  # published — nothing left to clean up
        return old_value, new_value
    except Exception:
        if owns_session:
            # Caller-supplied sessions are the caller's transaction to roll
            # back or not — rolling it back here would silently discard any
            # other pending, uncommitted work they'd already added to it.
            session.rollback()
        raise
    finally:
        if tmp_path is not None:
            os.remove(tmp_path)  # commit (or something before it) failed — discard the staged edit
        if owns_session:
            session.close()


def update_extra_field(vendor_id, column_name, new_value, source=ChangeSource.UI_EDIT, session=None, excel_path=None):
    """Generic-passthrough-column edit (data-pipeline-upload task,
    docs/11) — same DB-write/audit-log/atomic-Excel-write-back contract as
    update_vendor_field() above, extended to an arbitrary column position
    (VendorExtraField.column_name, the literal Excel header text) instead of
    one of the three hardcoded known fields. No validation against a known
    set — by definition this is a column the mapping layer doesn't
    recognize (Part B of that task); whatever Finance typed/picked is
    stored and written back as-is (never guessed at semantically).
    new_value is always coerced to text (None clears it).

    Same session-ownership contract as update_vendor_field(): owns its own
    session (session=None) -> stage, commit, publish, in that order; a
    caller-supplied session is DB-only, no Excel touch, no commit — the
    caller owns that transaction.

    Returns (old_value, new_value).
    """
    excel_path = excel_path or EXCEL_PATH
    new_value = None if new_value is None else str(new_value)

    owns_session = session is None
    session = session or SessionLocal()
    tmp_path = None
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        extra_row = session.query(VendorExtraField).filter_by(vendor_id=vendor_id, column_name=column_name).first()
        old_value = extra_row.value if extra_row is not None else None
        if extra_row is None:
            session.add(VendorExtraField(vendor_id=vendor_id, column_name=column_name, value=new_value))
        else:
            extra_row.value = new_value
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=vendor_id,
                field_name=column_name,
                old_value=old_value,
                new_value=new_value,
                source=source.value,
            )
        )
        session.flush()  # surface any DB-side error before touching Excel at all

        if not owns_session:
            return old_value, new_value

        tmp_path = _stage_excel_cell_write(column_name, vendor.erp_code, new_value, excel_path)
        session.commit()  # publish only happens below, after this succeeds
        os.replace(tmp_path, excel_path)
        tmp_path = None  # published — nothing left to clean up
        return old_value, new_value
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if tmp_path is not None:
            os.remove(tmp_path)
        if owns_session:
            session.close()
