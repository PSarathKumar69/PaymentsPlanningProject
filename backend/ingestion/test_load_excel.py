"""Self-check for the ingestion pipeline. Run with: pytest backend/ingestion/test_load_excel.py"""
import os
import shutil
import sys
import tempfile
from datetime import date

import openpyxl
import pytest

from backend.ingestion.column_mapping import parse_assigned_week_order, to_number


def _load_against_fresh_db():
    """backend.db.session reads its DB path once at import time, so each
    test that needs its own throwaway DB must force a re-import after
    pointing PAYMENTS_DB_PATH at a fresh temp file."""
    os.environ["PAYMENTS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    for mod in ("backend.db.session", "backend.ingestion.load_excel"):
        sys.modules.pop(mod, None)
    from backend.ingestion.load_excel import load

    return load()


def test_parse_assigned_week_order():
    assert parse_assigned_week_order("W2-P3") == (2, 3)
    assert parse_assigned_week_order(0) == (None, None)
    assert parse_assigned_week_order("0") == (None, None)
    assert parse_assigned_week_order(None) == (None, None)


def test_to_number():
    assert to_number(None) == 0.0
    assert to_number(" ") == 0.0
    assert to_number(5000) == 5000.0
    assert to_number("5000") == 5000.0


def test_load_duplicate_erp_code_same_entity_first_row_wins_and_warns_loudly(tmp_path):
    """Confirmed real bug (2026-07-31 investigation), narrowed by the
    entity-code-collision fix: a SAME-ENTITY ERP code collision (e.g. the
    real sheet's ARS "INC" pair — two different vendors typo'd onto one
    code) is a genuine data-entry error, not two entities legitimately
    sharing a bare code (see the different-entity test below for that
    case). The per-(entity, erp_code) upsert must still not let the later
    row silently overwrite the earlier one. First-row-wins (Sarath's later
    call, superseding the original last-row-wins fix): the later row is
    skipped entirely, never even read — loud either way, via a note naming
    the code, entity, every row, and the winner.
    """
    fixtures_dir = os.path.join(os.path.dirname(__file__), "test_fixtures")
    path = str(tmp_path / "dup.xlsx")
    shutil.copy(os.path.join(fixtures_dir, "single_header_row_sheet.xlsx"), path)

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    from backend.ingestion.column_mapping import build_sheet_map

    sheet_map = build_sheet_map(ws)
    # Fixture's two real rows: VT0001/"Alpha Freight Services" (row 2),
    # VT0002/"Bravo Industrial Supplies" (row 3), both entity "ARS" — give
    # row 3 row 2's own erp_code (same entity on both), matching the real
    # sheet's ARS "INC" collision shape exactly.
    ws.cell(row=3, column=sheet_map.erp_code_col, value="VT0001")
    wb.save(path)

    os.environ["PAYMENTS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    for mod in ("backend.db.session", "backend.ingestion.load_excel"):
        sys.modules.pop(mod, None)
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import load

    report = load(excel_path=path)

    assert len(report["duplicate_erp_code_notes"]) == 1
    note = report["duplicate_erp_code_notes"][0]
    assert "VT0001" in note and "ARS" in note and "Alpha Freight Services" in note  # names code, entity, winner
    assert "distinct ERP code" in note  # explicit, not left looking resolved
    assert note in report["data_quality_notes"]  # also surfaced through the existing notes channel

    session = SessionLocal()
    try:
        assert session.query(Vendor).count() == 1  # collapsed to one row, upload still succeeds
        vendor = session.query(Vendor).filter_by(erp_code="VT0001").one()
        assert vendor.vendor_name == "Alpha Freight Services"  # first row (row 2) won
    finally:
        session.close()


def test_load_duplicate_erp_code_different_entities_both_kept(tmp_path):
    """Entity-code-collision fix, the actual bug: 28 real pairs in the
    master sheet are two DIFFERENT legal entities (ARS/FP) sharing one bare
    numeric code, disambiguated by the sheet's own "Unique" column — this
    is not a duplicate at all, and both vendors must be ingested as
    separate, live rows (Vendor's real uniqueness key is (entity,
    erp_code), models.py), not silently collapsed to one."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "test_fixtures")
    path = str(tmp_path / "dup_entities.xlsx")
    shutil.copy(os.path.join(fixtures_dir, "single_header_row_sheet.xlsx"), path)

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    from backend.ingestion.column_mapping import build_sheet_map

    sheet_map = build_sheet_map(ws)
    # Same bare erp_code as row 2, but a DIFFERENT entity — this is the
    # ARS/FP "V00149" shape, not a real duplicate.
    ws.cell(row=3, column=sheet_map.erp_code_col, value="VT0001")
    ws.cell(row=3, column=sheet_map.entity_col, value="FP")
    wb.save(path)

    os.environ["PAYMENTS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    for mod in ("backend.db.session", "backend.ingestion.load_excel"):
        sys.modules.pop(mod, None)
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import load

    report = load(excel_path=path)

    assert report["duplicate_erp_code_notes"] == []  # not flagged — different entities, not a real duplicate

    session = SessionLocal()
    try:
        vendors = session.query(Vendor).filter_by(erp_code="VT0001").order_by(Vendor.entity).all()
        assert len(vendors) == 2  # both entities' vendors kept, not collapsed
        assert {v.entity for v in vendors} == {"ARS", "FP"}
        assert {v.vendor_name for v in vendors} == {"Alpha Freight Services", "Bravo Industrial Supplies"}
    finally:
        session.close()


def test_load_negative_payment_cell_treated_as_overpayment(tmp_path):
    """Confirmed with Finance: a negative Payment cell means an over-payment
    (more than what was billed) recorded with the wrong sign — it must be
    applied as a real payment of that magnitude (balance goes DOWN), not
    added back on as extra debt (the old, backwards behavior)."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "test_fixtures")
    path = str(tmp_path / "negpay.xlsx")
    shutil.copy(os.path.join(fixtures_dir, "single_header_row_sheet.xlsx"), path)

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    from backend.ingestion.column_mapping import build_sheet_map

    sheet_map = build_sheet_map(ws)
    first_month, payment_col = sheet_map.payment_cols[0]
    ws.cell(row=2, column=payment_col, value=-332000)
    wb.save(path)

    os.environ["PAYMENTS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
    for mod in ("backend.db.session", "backend.ingestion.load_excel"):
        sys.modules.pop(mod, None)
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import load

    report = load(excel_path=path)
    assert any("negative payment cell" in note for note in report["data_quality_notes"])

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code=ws.cell(row=2, column=sheet_map.erp_code_col).value).one()
        row = (
            session.query(MonthlyLedger)
            .filter_by(vendor_id=vendor.id, month=first_month)
            .one()
        )
        assert row.payment == 332000  # stored corrected/positive, never the raw negative
        assert row.closing_balance == row.opening_balance + row.payable - 332000  # balance went DOWN
    finally:
        session.close()


def test_load_real_sheet_reconciles():
    report = _load_against_fresh_db()
    assert report["vendor_count"] == 83
    assert report["ledger_row_count"] == 83 * 14
    # NOT asserting ledger_mismatches == [] here: the real dummy sheet's own
    # Closing Balance/Total Payable/Total Payment cells are formulas whose
    # cached values are currently blank (openpyxl's write-back drops them —
    # see the investigation; accepted as lost for this dummy file). That
    # check is still run and still logged as a sanity signal, but it no
    # longer feeds opening_balance (see the test below) — its count is a
    # property of the sheet's current, external, mutable formula-cache
    # state, not something ingestion's own correctness should be judged
    # against.
    #
    # Confirmed known data-entry slip in the source sheet (see docs/07) —
    # one vendor's W1-W5 amounts don't sum into that row's Total column.
    assert len(report["week_total_mismatches"]) == 1


def test_opening_balance_is_always_the_recomputed_figure_not_the_sheets_cached_formula():
    """Fix 1's confirmed bug: vendor.opening_balance was read from the
    sheet's own Closing Balance cell (data_only=True) — a formula whose
    cached value openpyxl's write-back can silently drop workbook-wide.
    Today it's blank for every one of the 83 real vendors (0.0), yet every
    vendor's opening_balance below is non-trivial and matches the ledger's
    own independently-recomputed closing figure — proving the DB value now
    comes from the tranche walk, not from a sheet figure that happens to
    already agree (it doesn't, right now).
    """
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal

    _load_against_fresh_db()
    session = SessionLocal()
    vendors = session.query(Vendor).all()
    assert len(vendors) == 83
    for vendor in vendors:
        last_ledger_row = (
            session.query(MonthlyLedger)
            .filter_by(vendor_id=vendor.id)
            .order_by(MonthlyLedger.month)
            .all()[-1]
        )
        assert float(vendor.opening_balance) == pytest.approx(float(last_ledger_row.closing_balance), abs=1)
    assert any(float(v.opening_balance) > 1000 for v in vendors)  # not a coincidental all-zero match
    session.close()


def test_assigned_week_survives_reingestion_when_sheet_gives_nothing():
    """Fix 2's confirmed permanent bug: the legacy assigned-week column is a
    VLOOKUP into an external workbook this repo doesn't have, so it reads as
    blank on every load — not just the next one, forever. Without a
    fallback to the existing DB value, every future re-ingestion/rollover
    would silently wipe assigned_week for virtually every vendor.
    Constructed: give a vendor a real assigned_week directly in the DB
    (today's actual, already-correct state), re-ingest the same real sheet
    (whose legacy column is still blank), confirm it survives untouched —
    same shape as test_finance_edited_assigned_week_survives_reingestion,
    but for a vendor with nothing at all from either sheet source.
    """
    _load_against_fresh_db()
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import load

    session = SessionLocal()
    vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
    vendor_id = vendor.id
    vendor.assigned_week = 2  # a real value the sheet itself can no longer confirm or provide
    session.commit()
    session.close()

    load()  # re-ingestion of the same, still-corrupted real sheet, same DB

    session = SessionLocal()
    reloaded = session.query(Vendor).filter_by(id=vendor_id).one()
    assert reloaded.assigned_week == 2  # untouched — not wiped to None
    session.close()


def test_finance_edited_assigned_week_survives_reingestion():
    """Fix 1's confirmed bug: load() never upserted by erp_code, so a second
    run crashed outright (UNIQUE constraint on vendors.erp_code) — and even
    setting that aside, assigned_week was read from the stale legacy packed
    column instead of the dedicated column vendor_edits.py writes Finance's
    edits to, so a re-ingestion would have silently clobbered the edit.
    Constructed: edit V00400 via vendor_edits.py (writes to a temp Excel
    copy + this fresh DB), re-ingest that same temp Excel, confirm the edit
    survived and no duplicate vendor row was created.
    """
    _load_against_fresh_db()

    for mod in ("backend.configuration.vendor_edits",):
        sys.modules.pop(mod, None)
    from backend.configuration.vendor_edits import update_vendor_field
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import EXCEL_PATH, load

    session = SessionLocal()
    vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
    vendor_id, original_week = vendor.id, vendor.assigned_week
    session.close()

    excel_copy = os.path.join(tempfile.mkdtemp(), "copy.xlsx")
    shutil.copy(EXCEL_PATH, excel_copy)
    new_week = 4 if original_week != 4 else 3
    update_vendor_field(vendor_id, "assigned_week", new_week, excel_path=excel_copy)

    load(excel_path=excel_copy)  # re-ingestion, same DB

    session = SessionLocal()
    assert session.query(Vendor).filter_by(erp_code="V00400").count() == 1  # upserted, not duplicated
    reloaded = session.query(Vendor).filter_by(erp_code="V00400").one()
    assert reloaded.id == vendor_id
    assert reloaded.assigned_week == new_week  # survived — not reset to the legacy column's original value
    session.close()


def test_mislabeled_nov_column_resolves_by_position():
    """Regression test: the sheet's 8th payable column is headered "Nov'26"
    (a confirmed real typo) but is structurally Nov-25 — months are derived
    by position from the Apr-25 anchor, not parsed from the header text.
    """
    _load_against_fresh_db()
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal

    session = SessionLocal()
    vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
    nov_row = (
        session.query(MonthlyLedger)
        .filter_by(vendor_id=vendor.id, month=date(2025, 11, 1))
        .one()
    )
    assert float(nov_row.payable) == 6506736.0
    session.close()
