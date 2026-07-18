"""Self-check for the ingestion pipeline. Run with: pytest backend/ingestion/test_load_excel.py"""
import os
import shutil
import sys
import tempfile
from datetime import date

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
