"""Ingestion: load data/Vendor's Details.xlsx into the SQLite DB.

Populates vendors + monthly_ledger (one row per vendor per month) and one
plan_runs/plan_allocations snapshot from the sheet's existing W1-W5 +
combined assigned-week/order column. Then verifies ledger integrity and
the W1-W5-vs-Total reconciliation, logging (not fixing) any mismatches.

Safe to re-run: docs/07 confirms the master Excel is re-loaded/refreshed at
the start of each month, so this is an upsert by erp_code (update the
existing vendor + replace their monthly_ledger rows from the fresh sheet),
never a blind re-insert — erp_code is unique, so re-inserting would crash.
Finance's edits to category/commitment_months/assigned_week (vendor_edits.py)
are read back from the SAME dedicated columns they were written to, not
recomputed/reset to a default — CLAUDE.md rule 6: the Excel is the single
source of truth precisely because edits are written back into it.

Run with: python -m backend.ingestion.load_excel
"""
import logging
from datetime import datetime

import openpyxl

from backend.db.models import MonthlyLedger, PlanAllocation, PlanRun, Vendor, VendorExtraField
from backend.db.session import SessionLocal, init_db
from backend.ingestion.column_mapping import (
    CATEGORY_FROM_EXCEL_LABEL,
    FIELD_TO_EXCEL_HEADER,
    PRIORITY_TAG_VALUES,
    build_sheet_map,
    find_column,
    parse_assigned_week_order,
    resolve_header,
    to_number,
    unmapped_header_columns,
)
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import ChangeSource, VendorCategory

EXCEL_PATH = "data/Vendor's Details.xlsx"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("ingestion")


def load(excel_path=EXCEL_PATH, session=None, header_overrides=None):
    """session: optional externally-supplied session — e.g.
    backend/month_end/rollover.py passes its own session so re-ingestion and
    its subsequent payment-cycle reset commit together as one transaction.
    Defaults to owning its own session/commit, unchanged for every other
    existing caller (same owns_session convention already used by
    vendor_edits.py/payment_logging.py/planner.py/regeneration.py).

    header_overrides: optional {logical_field: header_text} (AI-mapping
    task) — a persisted AI-resolved header name, consulted before each
    field's fixed default. None (the default) reproduces this function's
    exact pre-AI-mapping behavior; every existing caller that doesn't pass
    this gets identical results to before.
    """
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active
    sheet_map = build_sheet_map(ws, header_overrides=header_overrides)

    # Found once, not created — a re-ingestion before any Finance edit has
    # ever happened must behave exactly as it did before these columns
    # existed (find_column never mutates the workbook).
    category_col = find_column(ws, resolve_header("category", header_overrides))
    commitment_months_col = find_column(ws, resolve_header("commitment_months", header_overrides))
    assigned_week_override_col = find_column(ws, resolve_header("assigned_week", header_overrides))
    priority_tag_col = find_column(ws, resolve_header("priority_tag", header_overrides))

    # Generic passthrough columns (data-pipeline-upload task, docs/11):
    # anything the mapping layer above doesn't recognize gets stored per
    # vendor as-is, never fed into any model (CLAUDE.md rule 3). Computed
    # once, walked per vendor in the same pass below — not a second parser.
    unmapped_columns = unmapped_header_columns(
        ws,
        sheet_map,
        extra_cols=(category_col, commitment_months_col, assigned_week_override_col, priority_tag_col),
    )

    init_db()
    owns_session = session is None
    session = session or SessionLocal()

    # Prefetched once (not per-vendor-per-column) so an existing extra-field
    # value is updated in place rather than duplicated — a brand-new vendor
    # simply has no entry here yet, which is fine, it just gets a fresh row.
    existing_extra_fields = {(r.vendor_id, r.column_name): r for r in session.query(VendorExtraField).all()}

    ledger_mismatches = []
    week_total_mismatches = []
    data_quality_notes = []
    erp_codes_in_file = []

    plan_run = PlanRun(
        created_at=datetime.now(),
        month=sheet_map.payable_cols[-1][0],
        model_used=ChangeSource.INGESTION.value,
        funds_figure=None,
    )
    session.add(plan_run)
    session.flush()  # get plan_run.id

    vendor_count = 0
    ledger_row_count = 0
    allocation_count = 0

    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        erp_code = ws.cell(row=row, column=sheet_map.erp_code_col).value
        if erp_code is None:
            continue  # trailing blank row
        erp_codes_in_file.append(erp_code)

        entity = ws.cell(row=row, column=sheet_map.entity_col).value
        vendor_name = ws.cell(row=row, column=sheet_map.vendor_name_col).value

        raw_opening = ws.cell(row=row, column=sheet_map.opening_balance_col).value
        if raw_opening is None:
            data_quality_notes.append(f"{erp_code}: blank Opening Balance, treated as 0")
        opening_balance = to_number(raw_opening)

        sheet_total_payable = to_number(ws.cell(row=row, column=sheet_map.total_payable_col).value)
        sheet_total_payment = to_number(ws.cell(row=row, column=sheet_map.total_payment_col).value)
        sheet_closing = to_number(ws.cell(row=row, column=sheet_map.closing_balance_col).value)

        raw_assigned_order = ws.cell(row=row, column=sheet_map.assigned_week_order_col).value
        legacy_assigned_week, within_week_order = parse_assigned_week_order(raw_assigned_order)

        existing = session.query(Vendor).filter_by(erp_code=erp_code).first()

        # category/commitment_months: an edit always writes a real label/
        # number into the dedicated cell, never blank (see vendor_edits.py's
        # _validate — commitment_months can't be None, and even resetting
        # category to Normal writes the literal "Normal") — so a blank cell
        # unambiguously means "never edited," safe to fall back to the
        # existing DB value (or the model default, for a brand-new vendor).
        category_raw = ws.cell(row=row, column=category_col).value if category_col else None
        if category_raw is not None:
            category = CATEGORY_FROM_EXCEL_LABEL.get(str(category_raw).strip(), VendorCategory.NORMAL)
        elif existing is not None:
            category = existing.category
        else:
            category = VendorCategory.NORMAL

        commitment_months_raw = (
            ws.cell(row=row, column=commitment_months_col).value if commitment_months_col else None
        )
        if commitment_months_raw is not None:
            commitment_months = int(to_number(commitment_months_raw))
        elif existing is not None:
            commitment_months = existing.commitment_months
        else:
            commitment_months = None  # let the model's own column default (1) apply on insert

        # assigned_week: the dedicated column (Finance's UI edits) wins over
        # the legacy packed column whenever either has a value for this
        # vendor; if BOTH are blank, fall back to the existing DB value —
        # same pattern as category/commitment_months above. This isn't just
        # a one-off safety net for a single bad load: the legacy column is a
        # VLOOKUP into an external workbook this repo doesn't have, so it
        # reads as blank on EVERY future load, permanently. This fallback is
        # therefore the permanent mechanism that keeps assigned_week intact
        # across every future re-ingestion/rollover, not a stopgap.
        assigned_week_override_raw = (
            ws.cell(row=row, column=assigned_week_override_col).value if assigned_week_override_col else None
        )
        sheet_assigned_week = (
            int(assigned_week_override_raw) if assigned_week_override_raw is not None else legacy_assigned_week
        )
        if sheet_assigned_week is not None:
            assigned_week = sheet_assigned_week
        elif existing is not None:
            assigned_week = existing.assigned_week
        else:
            assigned_week = None  # brand-new vendor, sheet gave nothing to seed from

        # priority_tag (AI-mapping task's Part A prerequisite): same
        # blank-means-never-edited fallback as category/commitment_months
        # above. Unlike category (which silently defaults an unrecognized
        # label to Normal), an unrecognized priority-tag value logs a
        # data-quality note and falls back rather than guessing — losing a
        # previously-set tag silently felt like the wrong default here,
        # flagging it is the documented, reasonable one.
        priority_tag_raw = ws.cell(row=row, column=priority_tag_col).value if priority_tag_col else None
        if priority_tag_raw is not None:
            priority_tag_text = str(priority_tag_raw).strip().upper()
            if priority_tag_text in PRIORITY_TAG_VALUES:
                # Plain string (Vendor.priority_tag is a plain String column,
                # this task's fix) — priority_tag_text is already the exact
                # validated value, no need to round-trip through the enum.
                priority_tag = priority_tag_text
            else:
                data_quality_notes.append(
                    f"{erp_code}: unrecognized Priority Tag {priority_tag_raw!r}, kept previous value"
                )
                priority_tag = existing.priority_tag if existing is not None else None
        elif existing is not None:
            priority_tag = existing.priority_tag
        else:
            priority_tag = None

        if existing is not None:
            vendor = existing
            vendor.entity = entity
            vendor.vendor_name = vendor_name
            # opening_balance is set below, after the ledger walk recomputes
            # it — never from sheet_closing directly (see that assignment
            # for why).
            vendor.assigned_week = assigned_week
            vendor.category = category
            vendor.priority_tag = priority_tag
            # Present in this sheet -> live, whether or not a prior upload
            # had deactivated it (Bug #1 fix, models.py's is_active comment).
            vendor.is_active = True
            vendor.removed_at = None
            if commitment_months is not None:
                vendor.commitment_months = commitment_months
            # Ledger figures are sheet-derived, never Finance-edited — safe
            # to drop and rebuild rather than diff month-by-month.
            session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).delete()
        else:
            vendor = Vendor(
                erp_code=erp_code,
                entity=entity,
                vendor_name=vendor_name,
                opening_balance=0.0,  # placeholder — overwritten below, after the ledger walk recomputes it
                assigned_week=assigned_week,
                category=category,
                priority_tag=priority_tag,
                reconsider=None,
                score=None,
                current_aging_bucket=None,
            )
            if commitment_months is not None:
                vendor.commitment_months = commitment_months
            session.add(vendor)
        session.flush()  # get vendor.id
        vendor_count += 1

        touched_extra_columns = set()
        for col_idx, header_text in unmapped_columns:
            raw = ws.cell(row=row, column=col_idx).value
            value_text = None if raw is None else str(raw).strip()
            key = (vendor.id, header_text)
            extra_row = existing_extra_fields.get(key)
            if extra_row is not None:
                extra_row.value = value_text
            else:
                extra_row = VendorExtraField(vendor_id=vendor.id, column_name=header_text, value=value_text)
                session.add(extra_row)
                existing_extra_fields[key] = extra_row
            touched_extra_columns.add(header_text)

        # A column that existed on a PRIOR load but is absent from this
        # sheet entirely (e.g. revert to a backup taken before that column
        # existed) is dropped for this vendor — same "sheet-derived, rebuilt
        # every load" convention monthly_ledger already follows above, not
        # a diff/merge against whatever used to be there.
        for (vid, column_name), extra_row in list(existing_extra_fields.items()):
            if vid == vendor.id and column_name not in touched_extra_columns:
                session.delete(extra_row)
                del existing_extra_fields[(vid, column_name)]

        # Walk the monthly columns to build the per-month ledger, then
        # verify against the sheet's own reported totals/closing balance.
        running_opening = opening_balance
        sum_payable = 0.0
        sum_payment = 0.0
        for (month, payable_col), (_, payment_col) in zip(sheet_map.payable_cols, sheet_map.payment_cols):
            payable = to_number(ws.cell(row=row, column=payable_col).value)
            payment = to_number(ws.cell(row=row, column=payment_col).value)
            if isinstance(ws.cell(row=row, column=payment_col).value, str):
                data_quality_notes.append(
                    f"{erp_code}: non-numeric payment cell for {month:%b-%y}, treated as 0"
                )
            closing = running_opening + payable - payment
            session.add(
                MonthlyLedger(
                    vendor_id=vendor.id,
                    month=month,
                    payable=payable,
                    payment=payment,
                    opening_balance=running_opening,
                    closing_balance=closing,
                )
            )
            ledger_row_count += 1
            sum_payable += payable
            sum_payment += payment
            running_opening = closing

        # Current balance = latest recomputed closing, carried forward.
        # Deliberately NOT sheet_closing: that's a formula cell
        # (Closing Balance = Op. Balance + Total Payable - Total Payment),
        # and openpyxl's load-modify-save cycle (vendor_edits.py's Excel
        # write-back) silently drops every formula cell's cached value
        # workbook-wide on save — not just the cell that was edited. This
        # figure is computed purely from the raw (literal, never-a-formula)
        # Payable/Payment columns walked above, so it can never be affected
        # by that bug. The comparison below is kept as a sanity-check signal
        # only, never as the value that actually lands in the DB.
        vendor.opening_balance = running_opening

        if abs(running_opening - sheet_closing) > MONEY_EPSILON:
            ledger_mismatches.append((erp_code, running_opening, sheet_closing))
        if abs(sum_payable - sheet_total_payable) > MONEY_EPSILON:
            data_quality_notes.append(
                f"{erp_code}: recomputed Total Payable {sum_payable:.2f} != sheet's {sheet_total_payable:.2f}"
            )
        if abs(sum_payment - sheet_total_payment) > MONEY_EPSILON:
            data_quality_notes.append(
                f"{erp_code}: recomputed Total Payment {sum_payment:.2f} != sheet's {sheet_total_payment:.2f}"
            )

        # W1-W5 breakdown -> plan_allocations, one row per non-zero week.
        # A vendor can have money in more than one week in the same cycle
        # (e.g. a partial payment topped up later) — see docs/06.
        week_values = [
            (week_no, to_number(ws.cell(row=row, column=col).value))
            for week_no, col in sheet_map.week_cols
        ]
        sheet_week_total = to_number(ws.cell(row=row, column=sheet_map.week_total_col).value)
        actual_week_sum = sum(v for _, v in week_values)
        if abs(actual_week_sum - sheet_week_total) > MONEY_EPSILON:
            week_total_mismatches.append((erp_code, actual_week_sum, sheet_week_total))

        for week_no, amount in week_values:
            if amount == 0:
                continue
            session.add(
                PlanAllocation(
                    plan_run_id=plan_run.id,
                    vendor_id=vendor.id,
                    assigned_week=week_no,
                    within_week_order=within_week_order,
                    allocated_amount=amount,
                )
            )
            allocation_count += 1

    # Replace, not merge (Bug #1 fix): any currently-active vendor whose
    # erp_code didn't turn up anywhere in this sheet is soft-deactivated —
    # see models.py's Vendor.is_active comment for why not a hard delete.
    # Their ledger/extra-field rows are sheet-derived and safe to drop, same
    # as the existing-vendor branch above already does for vendors still
    # present.
    erp_codes_in_file_set = set(erp_codes_in_file)
    deactivated_erp_codes = []
    for vendor in session.query(Vendor).filter(Vendor.is_active.isnot(False)).all():
        if vendor.erp_code not in erp_codes_in_file_set:
            vendor.is_active = False
            vendor.removed_at = datetime.now()
            session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).delete()
            session.query(VendorExtraField).filter_by(vendor_id=vendor.id).delete()
            deactivated_erp_codes.append(vendor.erp_code)

    if owns_session:
        session.commit()
        session.close()

    return {
        "vendor_count": vendor_count,
        "ledger_row_count": ledger_row_count,
        "allocation_count": allocation_count,
        "ledger_mismatches": ledger_mismatches,
        "week_total_mismatches": week_total_mismatches,
        "data_quality_notes": data_quality_notes,
        "erp_codes_in_file": erp_codes_in_file,
        "deactivated_vendor_count": len(deactivated_erp_codes),
        "deactivated_erp_codes": deactivated_erp_codes,
        "unmapped_columns": [header for _, header in unmapped_columns],
    }


def print_report(report):
    log.info("Vendors loaded: %d", report["vendor_count"])
    log.info("Monthly ledger rows loaded: %d", report["ledger_row_count"])
    log.info("Plan allocation rows loaded: %d", report["allocation_count"])

    if report["ledger_mismatches"]:
        log.warning("Ledger-integrity mismatches (Closing != Opening + Payable - Payment):")
        for erp_code, computed, expected in report["ledger_mismatches"]:
            log.warning("  %s: computed=%.2f, sheet=%.2f", erp_code, computed, expected)
    else:
        log.info("Ledger integrity: OK for all vendors.")

    if report["week_total_mismatches"]:
        log.warning("W1-W5 sum vs sheet's Total column mismatches (known entry-error pattern):")
        for erp_code, computed, expected in report["week_total_mismatches"]:
            log.warning("  %s: W1-W5 sum=%.2f, sheet Total=%.2f", erp_code, computed, expected)
    else:
        log.info("W1-W5 vs Total: OK for all vendors.")

    if report["data_quality_notes"]:
        log.warning("Other data-quality notes:")
        for note in report["data_quality_notes"]:
            log.warning("  %s", note)


if __name__ == "__main__":
    print_report(load())
