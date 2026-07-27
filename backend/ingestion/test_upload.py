"""Tests for the Excel upload/commit/revert flow (data-pipeline-upload
task, docs/07 + docs/11). Same isolation convention as test_load_excel.py
(fresh temp DB via PAYMENTS_DB_PATH + module re-import) plus a tmp_path copy
of the real master Excel for every file operation — the real
data/Vendor's Details.xlsx and backend/db/app.db are never touched.

REVISED (Sarath's course-correction, this task): there is no preview step
any more — commit_upload() is the whole flow, one call. The diff-detection
tests below now exercise that same diff logic through commit_upload()'s own
response instead of a since-removed preview_upload().
"""
import os
import shutil
import sys

import openpyxl
import pytest


def _fresh_db_and_master(tmp_path):
    """Fresh temp DB, seeded with a baseline ingestion of a tmp_path copy of
    the real master sheet — mirrors test_load_excel.py's own
    _load_against_fresh_db() helper exactly, including its narrow pop list
    (just these two modules, not "every backend.* module"): popping wider
    than that (confirmed the hard way) leaves any OTHER already-imported
    module — e.g. backend.models.new_model_2.allocator, whose own test file
    does `from ... import generate_plan` at collection time — holding a
    stale reference once this function re-imports load_excel/session fresh,
    so a later test's `import allocator_module` gets a DIFFERENT module
    object than the generate_plan it already imported, and monkeypatching
    the former silently does nothing to the latter. Every test below passes
    explicit excel_path/backup_path overrides anyway, so upload.py's own
    module-level EXCEL_PATH/MASTER_EXCEL_BACKUP_PATH constants are never
    read from a stale copy either way — no need to pop that module too."""
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    # Every module a test below actually imports from and that itself calls
    # SessionLocal() (directly, or via a bare `session=None` default) needs
    # popping here — not just backend.db.session itself, or a stale cached
    # copy of e.g. backend.ingestion.upload would keep calling the OLD
    # SessionLocal it captured on its first import in an earlier test.
    for mod in (
        "backend.db.session",
        "backend.ingestion.load_excel",
        "backend.ingestion.upload",
        "backend.configuration.vendor_edits",
        "backend.configuration.extra_fields",
    ):
        sys.modules.pop(mod, None)
    from backend.ingestion.load_excel import EXCEL_PATH, load

    master_path = str(tmp_path / "master.xlsx")
    shutil.copy(EXCEL_PATH, master_path)
    load(excel_path=master_path)
    return master_path


def _add_unmapped_column(src_path, dest_path, header, value_fn):
    """value_fn(erp_code, row_offset) -> cell value, for every real data row."""
    from backend.ingestion.column_mapping import build_sheet_map

    wb = openpyxl.load_workbook(src_path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    col = ws.max_column + 1
    ws.cell(row=3, column=col, value=header)
    offset = 0
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        erp_code = ws.cell(row=row, column=sheet_map.erp_code_col).value
        if erp_code is None:
            continue
        ws.cell(row=row, column=col, value=value_fn(erp_code, offset))
        offset += 1
    wb.save(dest_path)


def _change_vendor_cell(src_path, dest_path, erp_code, header, new_value):
    from backend.ingestion.column_mapping import build_sheet_map, find_column

    wb = openpyxl.load_workbook(src_path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    col = find_column(ws, header)
    assert col is not None, f"column {header!r} not found"
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == erp_code:
            ws.cell(row=row, column=col, value=new_value)
            wb.save(dest_path)
            return
    raise AssertionError(f"vendor {erp_code!r} not found")


def _read_vendor_cell(path, erp_code, header):
    from backend.ingestion.column_mapping import build_sheet_map, find_column

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    col = find_column(ws, header)
    assert col is not None, f"column {header!r} not found"
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == erp_code:
            return ws.cell(row=row, column=col).value
    raise AssertionError(f"vendor {erp_code!r} not found")


def _remove_vendor_rows(src_path, dest_path, erp_codes):
    """Bug #1 test helper — builds an upload sheet with the given vendors'
    rows entirely absent (not just blanked), the same shape as Sarath's
    real repro (a replacement sheet that doesn't list some previously-known
    vendors at all)."""
    from backend.ingestion.column_mapping import build_sheet_map

    wb = openpyxl.load_workbook(src_path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    to_remove = set(erp_codes)
    for row in range(ws.max_row, sheet_map.data_start_row - 1, -1):  # bottom-up so row numbers stay valid
        if ws.cell(row=row, column=sheet_map.erp_code_col).value in to_remove:
            ws.delete_rows(row, 1)
    wb.save(dest_path)


def _duplicate_row_as_new_vendor(src_path, dest_path, source_erp_code, new_erp_code):
    from backend.ingestion.column_mapping import build_sheet_map

    wb = openpyxl.load_workbook(src_path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == source_erp_code:
            new_row = ws.max_row + 1
            for col in range(1, ws.max_column + 1):
                ws.cell(row=new_row, column=col, value=ws.cell(row=row, column=col).value)
            ws.cell(row=new_row, column=sheet_map.erp_code_col, value=new_erp_code)
            ws.cell(row=new_row, column=sheet_map.vendor_name_col, value="Test New Vendor")
            wb.save(dest_path)
            return
    raise AssertionError(f"vendor {source_erp_code!r} not found")


# ---- Unit tests: diff computation, exercised through commit_upload() ------
# (no more standalone preview_upload() — Sarath's course-correction, this
# task — but commit_upload() computes and returns this same diff, it just
# also writes at the same time now.)


def test_commit_detects_new_vendor(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.ingestion.upload import commit_upload

    upload_path = str(tmp_path / "upload.xlsx")
    _duplicate_row_as_new_vendor(master_path, upload_path, "V00400", "V99999")
    backup_path = str(tmp_path / "master.backup.xlsx")

    result = commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)
    assert "V99999" in result["new_vendors"]
    assert result["new_vendor_count"] == 1
    assert result["existing_vendor_count"] == 83  # every other real vendor, unchanged


def test_commit_detects_changed_ledger_figure(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.ingestion.upload import commit_upload

    upload_path = str(tmp_path / "upload.xlsx")
    _change_vendor_cell(master_path, upload_path, "V00400", "Apr-25", 999_999_999)
    backup_path = str(tmp_path / "master.backup.xlsx")

    result = commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)
    assert "V00400" in result["vendors_with_changed_ledger"]
    assert result["vendors_with_changed_ledger_count"] == 1
    assert any("Apr-25" in c for c in result["changed_fields_by_vendor"]["V00400"])


def test_commit_detects_unmapped_column(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.ingestion.upload import commit_upload

    upload_path = str(tmp_path / "upload.xlsx")
    _add_unmapped_column(master_path, upload_path, "Status", lambda erp, i: "Active" if i % 2 == 0 else "Inactive")
    backup_path = str(tmp_path / "master.backup.xlsx")

    result = commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)
    assert "Status" in result["unmapped_columns"]
    assert "Unique" in result["unmapped_columns"]  # already-known real unmapped columns, still reported
    assert "VN" in result["unmapped_columns"]


# ---- Integration tests: full upload -> commit -> DB+Excel state -----------


def test_commit_updates_db_and_master_excel_to_match_upload(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload
    from datetime import date

    upload_path = str(tmp_path / "upload.xlsx")
    _change_vendor_cell(master_path, upload_path, "V00400", "Apr-25", 12_345_678)
    backup_path = str(tmp_path / "master.backup.xlsx")

    result = commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)
    assert result["vendors_changed"] >= 1

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
        ledger_row = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id, month=date(2025, 4, 1)).one()
        assert float(ledger_row.payable) == pytest.approx(12_345_678)
    finally:
        session.close()

    # Master Excel now IS the uploaded file (atomically replaced) — reload
    # it fresh and confirm the same cell reads back the new value.
    from backend.ingestion.column_mapping import build_sheet_map, find_column

    wb = openpyxl.load_workbook(master_path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    col = find_column(ws, "Apr-25")
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == "V00400":
            assert ws.cell(row=row, column=col).value == 12_345_678
            break
    else:
        raise AssertionError("V00400 not found in post-commit master file")


def test_commit_writes_audit_log_entries_with_excel_upload_source(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.models import AuditLog, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload
    from backend.shared.enums import ChangeSource

    upload_path = str(tmp_path / "upload.xlsx")
    _change_vendor_cell(master_path, upload_path, "V00400", "Apr-25", 12_345_678)
    backup_path = str(tmp_path / "master.backup.xlsx")
    commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
        entry = (
            session.query(AuditLog)
            .filter_by(vendor_id=vendor.id, source=ChangeSource.EXCEL_UPLOAD.value)
            .one()
        )
        assert "Apr-25" in entry.new_value
        assert entry.old_value is None  # per-vendor summary, not a cell-by-cell before/after log
    finally:
        session.close()


# ---- Integration test: backup -> bad upload -> revert ---------------------


def test_backup_bad_upload_revert_restores_pre_upload_state(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload, revert_upload
    from datetime import date

    session = SessionLocal()
    original_payable = float(
        session.query(MonthlyLedger)
        .join(Vendor)
        .filter(Vendor.erp_code == "V00400", MonthlyLedger.month == date(2025, 4, 1))
        .one()
        .payable
    )
    session.close()

    bad_upload_path = str(tmp_path / "bad_upload.xlsx")
    _change_vendor_cell(master_path, bad_upload_path, "V00400", "Apr-25", -1)  # obviously "bad" data
    backup_path = str(tmp_path / "master.backup.xlsx")
    commit_upload(bad_upload_path, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    bad_value = float(
        session.query(MonthlyLedger)
        .join(Vendor)
        .filter(Vendor.erp_code == "V00400", MonthlyLedger.month == date(2025, 4, 1))
        .one()
        .payable
    )
    session.close()
    assert bad_value == pytest.approx(-1)  # confirms the bad upload actually landed before we revert it

    revert_result = revert_upload(excel_path=master_path, backup_path=backup_path)
    assert "not covered by this revert" in revert_result["warning"]

    session = SessionLocal()
    try:
        restored_value = float(
            session.query(MonthlyLedger)
            .join(Vendor)
            .filter(Vendor.erp_code == "V00400", MonthlyLedger.month == date(2025, 4, 1))
            .one()
            .payable
        )
        assert restored_value == pytest.approx(original_payable)
    finally:
        session.close()

    wb = openpyxl.load_workbook(master_path)
    ws = wb.active
    from backend.ingestion.column_mapping import build_sheet_map, find_column

    sheet_map = build_sheet_map(ws)
    col = find_column(ws, "Apr-25")
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == "V00400":
            assert ws.cell(row=row, column=col).value != -1
            break


# ---- Bug #1: replace-upload must actually replace, not merge/append -------


def test_commit_soft_deactivates_vendor_missing_from_upload(tmp_path):
    """The repro: a vendor whose erp_code is absent from the newly uploaded
    sheet must disappear from the live vendor list/grid (soft-deactivated),
    not stay live alongside the new data — and never be hard-deleted, since
    PlanAllocation/Payment/AuditLog rows already reference it and SQLite FK
    enforcement is off here (models.py's Vendor.is_active comment).

    Picks its own victim erp_codes from whatever master sheet is actually
    loaded rather than hardcoding "V00400" — the real data/Vendor's
    Details.xlsx this copies from is a shared, externally-mutable file (see
    memory: demo15-dataset-quirks) that has drifted vendor codes before."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.models import AuditLog, MonthlyLedger, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.grid import build_master_grid
    from backend.ingestion.upload import commit_upload
    from backend.shared.enums import ChangeSource

    session = SessionLocal()
    total_before = session.query(Vendor).count()
    removed = [v.erp_code for v in session.query(Vendor).order_by(Vendor.id).limit(2).all()]
    session.close()
    assert len(removed) == 2  # sanity: master sheet has at least 2 vendors to remove

    upload_path = str(tmp_path / "upload.xlsx")
    _remove_vendor_rows(master_path, upload_path, removed)
    backup_path = str(tmp_path / "master.backup.xlsx")

    result = commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)
    assert result["deactivated_vendor_count"] == 2
    assert set(result["deactivated_erp_codes"]) == set(removed)

    session = SessionLocal()
    try:
        # Row still physically present (not hard-deleted) but flagged inactive,
        # and its sheet-derived rows are dropped.
        for erp_code in removed:
            vendor = session.query(Vendor).filter_by(erp_code=erp_code).one()
            assert vendor.is_active is False
            assert vendor.removed_at is not None
            assert session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).count() == 0
            entry = (
                session.query(AuditLog)
                .filter_by(vendor_id=vendor.id, source=ChangeSource.EXCEL_UPLOAD.value)
                .one()
            )
            assert "removed" in entry.new_value.lower()

        # Total row count is unchanged (soft-deactivate, not delete)...
        assert session.query(Vendor).count() == total_before
        # ...but every live-facing read excludes them: Master Data grid,
        # min-funds' shared vendor loader — replaced, not merged.
        grid = build_master_grid(session, excel_path=master_path)
        assert {v["erp_code"] for v in grid["vendors"]} & set(removed) == set()
        assert len(grid["vendors"]) == total_before - 2

        from backend.shared.min_funds import _load_vendors_and_ledger

        live_vendors, _ = _load_vendors_and_ledger(session)
        assert {v.erp_code for v in live_vendors} & set(removed) == set()
    finally:
        session.close()


def test_revert_after_deactivating_upload_restores_the_vendor(tmp_path):
    """The one-slot backup/revert flow must still work exactly as before:
    reverting past a replace-upload that dropped some vendors brings them
    back live (reactivated, not re-inserted as a new row)."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload, revert_upload

    session = SessionLocal()
    total_before = session.query(Vendor).count()
    target = session.query(Vendor).order_by(Vendor.id).first()
    target_erp_code, original_vendor_id = target.erp_code, target.id
    session.close()

    upload_path = str(tmp_path / "upload.xlsx")
    _remove_vendor_rows(master_path, upload_path, [target_erp_code])
    backup_path = str(tmp_path / "master.backup.xlsx")
    commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    assert session.query(Vendor).filter_by(erp_code=target_erp_code).one().is_active is False
    session.close()

    revert_upload(excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code=target_erp_code).one()
        assert vendor.is_active is True
        assert vendor.removed_at is None
        assert vendor.id == original_vendor_id  # reactivated the same row, not re-inserted
        assert session.query(Vendor).count() == total_before  # no duplicate row created
    finally:
        session.close()


def test_revert_without_a_prior_upload_raises_clean_error(tmp_path):
    _fresh_db_and_master(tmp_path)
    from backend.ingestion.upload import revert_upload

    with pytest.raises(ValueError, match="no backup available"):
        revert_upload(excel_path=str(tmp_path / "master.xlsx"), backup_path=str(tmp_path / "nonexistent.backup.xlsx"))


# ---- Round-trip test: VendorExtraField (widget derivation + edit) ---------


def test_extra_field_round_trip_widget_derivation_and_edit(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)
    from backend.configuration.extra_fields import list_extra_fields
    from backend.configuration.vendor_edits import update_extra_field
    from backend.db.models import AuditLog, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload

    # 2-value column -> toggle.
    upload_1 = str(tmp_path / "upload1.xlsx")
    _add_unmapped_column(master_path, upload_1, "Toggle Col", lambda erp, i: "Yes" if i % 2 == 0 else "No")
    backup_path = str(tmp_path / "master.backup.xlsx")
    commit_upload(upload_1, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    fields = {f["column_name"]: f for f in list_extra_fields(session)}
    session.close()
    assert fields["Toggle Col"]["widget"] == "toggle"
    assert set(fields["Toggle Col"]["options"]) == {"Yes", "No"}

    # 5-value column -> dropdown with those exact 5 options.
    upload_2 = str(tmp_path / "upload2.xlsx")
    five_values = ["A", "B", "C", "D", "E"]
    _add_unmapped_column(master_path, upload_2, "Dropdown Col", lambda erp, i: five_values[i % 5])
    commit_upload(upload_2, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    fields = {f["column_name"]: f for f in list_extra_fields(session)}
    vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
    vendor_id = vendor.id
    session.close()
    assert fields["Dropdown Col"]["widget"] == "dropdown"
    assert set(fields["Dropdown Col"]["options"]) == set(five_values)

    # Edit one vendor's value through the new endpoint-backing function —
    # confirm DB, audit_log, and the next Excel read all agree.
    excel_copy = str(tmp_path / "edit_copy.xlsx")
    shutil.copy(master_path, excel_copy)
    old_value, new_value = update_extra_field(vendor_id, "Dropdown Col", "Z", excel_path=excel_copy)
    assert new_value == "Z"

    session = SessionLocal()
    try:
        fields = {f["column_name"]: f for f in list_extra_fields(session)}
        assert fields["Dropdown Col"]["values_by_vendor_id"][vendor_id] == "Z"
        entry = session.query(AuditLog).filter_by(vendor_id=vendor_id, field_name="Dropdown Col").one()
        assert entry.new_value == "Z"
    finally:
        session.close()

    from backend.ingestion.column_mapping import build_sheet_map, find_column

    wb = openpyxl.load_workbook(excel_copy)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    col = find_column(ws, "Dropdown Col")
    for row in range(sheet_map.data_start_row, ws.max_row + 1):
        if ws.cell(row=row, column=sheet_map.erp_code_col).value == "V00400":
            assert ws.cell(row=row, column=col).value == "Z"
            break
    else:
        raise AssertionError("V00400 not found when reading back the edited column")


# ---- End-to-end (Fix 3): uploaded data actually reaches Planning/Payments -
# generate_plan()/calculate_minimum_funds_required()/log_payment() all
# default to SessionLocal() when no session is passed — a stale cached copy
# of THEIR module (backend.models.new_model_2.allocator,
# backend.shared.min_funds, backend.shared.payment_logging) from an earlier
# test elsewhere in the same pytest process would silently keep talking to
# an old temp DB via that internal default instead of this test's fresh
# one. Popping those modules to dodge that is what test_min_funds.py's own
# object-identity tests exist to catch (confirmed the hard way: doing so
# here broke test_model2_advisor_reexports_the_same_function_objects and
# test_model1_scorer_does_not_import_model2_package when the full suite ran
# — those modules import from min_funds and compare `is` identity, so
# popping min_funds out from under them mid-session breaks that check for
# reasons unrelated to any real bug). Fix: build ONE session from this
# file's own already-fresh backend.db.session.SessionLocal and pass it
# EXPLICITLY into every call below — the callee then never reaches its own
# internal SessionLocal default at all, so which module copy it happens to
# be attached to stops mattering, and no other test file's cache needs
# touching.


def test_upload_data_reaches_new_model_2_generate_plan(tmp_path):
    """Fix 3 — a vendor's ledger figure changed by an upload must show up
    in the very next New Model 2 generate_plan() call for that vendor, not
    a stale pre-upload number. Proves there's no cached session/vendor
    object anywhere between the upload path and the planning path."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload
    from backend.models.new_model_2.allocator import generate_plan
    from backend.shared.min_funds_v2 import calculate_minimum_funds_required_v2

    session = SessionLocal()
    try:
        before = calculate_minimum_funds_required_v2(session=session)
        before_breakdown = before["breakdown"]
        before_required = float(
            before_breakdown[before_breakdown["erp_code"] == "V00400"]["required_amount"].iloc[0]
        )
    finally:
        session.close()

    original_apr_payable = float(_read_vendor_cell(master_path, "V00400", "Apr-25") or 0)
    delta = 50_000_000.0
    upload_path = str(tmp_path / "upload.xlsx")
    _change_vendor_cell(master_path, upload_path, "V00400", "Apr-25", original_apr_payable + delta)
    backup_path = str(tmp_path / "master.backup.xlsx")
    commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    try:
        after = calculate_minimum_funds_required_v2(session=session)
        after_breakdown = after["breakdown"]
        after_required = float(
            after_breakdown[after_breakdown["erp_code"] == "V00400"]["required_amount"].iloc[0]
        )
        # Directional, not an exact-delta prediction: required_amount()'s
        # carry-forward walk (backend/shared/min_funds.py) can fold the
        # bumped tranche in alongside others already in its window, so the
        # exact increase isn't a simple +delta — that formula has its own
        # dedicated tests elsewhere. What matters here is that the figure
        # actually moved off the pre-upload value, proving generate_plan()
        # below reads live data, not something cached from before the upload.
        assert after_required > before_required
        assert after_required != pytest.approx(before_required)

        plan = generate_plan(available_funds=after["total"] * 2, session=session)  # absolute funds -> 100% for everyone
        row = plan["allocations"][plan["allocations"]["erp_code"] == "V00400"].iloc[0]
        assert float(row["required_amount"]) == pytest.approx(after_required, abs=1)
        assert float(row["allocated_amount"]) == pytest.approx(after_required, abs=1)
    finally:
        session.close()


def test_upload_data_reaches_payment_logging(tmp_path):
    """Fix 3 — log_payment() must apply against the vendor's balance as it
    stands AFTER the most recent upload. payment_amount below is bigger
    than the OLD (pre-upload) balance but well within the NEW (post-upload)
    one — if log_payment() read a stale pre-upload balance for its
    ledger-integrity guard, this would wrongly raise "payment exceeds what's
    owed" instead of succeeding."""
    master_path = _fresh_db_and_master(tmp_path)
    from datetime import date

    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload
    from backend.shared.payment_logging import log_payment

    session = SessionLocal()
    vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
    old_opening_balance = float(vendor.opening_balance)
    vendor_id = vendor.id
    session.close()

    original_apr_payable = float(_read_vendor_cell(master_path, "V00400", "Apr-25") or 0)
    delta = 50_000_000.0
    upload_path = str(tmp_path / "upload.xlsx")
    _change_vendor_cell(master_path, upload_path, "V00400", "Apr-25", original_apr_payable + delta)
    backup_path = str(tmp_path / "master.backup.xlsx")
    commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    new_opening_balance = float(session.query(Vendor).filter_by(id=vendor_id).one().opening_balance)
    session.close()
    assert new_opening_balance == pytest.approx(old_opening_balance + delta, abs=1)

    payment_amount = old_opening_balance + 10_000_000.0  # > old balance, well within the new one
    session = SessionLocal()
    try:
        log_payment(vendor_id, payment_amount, today=date(2026, 5, 8), session=session)  # raises if stale-balance-based
        session.commit()
    finally:
        session.close()

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        assert float(vendor.paid_so_far_this_month) == pytest.approx(payment_amount)
        live_outstanding = float(vendor.opening_balance) - float(vendor.paid_so_far_this_month)
        assert live_outstanding == pytest.approx(new_opening_balance - payment_amount, abs=1)
    finally:
        session.close()


if __name__ == "__main__":
    # ponytail: no framework/fixtures — a minimal manual run for a quick
    # sanity check (pytest's tmp_path fixture isn't available standalone).
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        test_commit_detects_new_vendor(tmp_path)
    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        test_extra_field_round_trip_widget_derivation_and_edit(tmp_path)
    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        test_upload_data_reaches_new_model_2_generate_plan(tmp_path)
    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        test_upload_data_reaches_payment_logging(tmp_path)
    print("upload.py self-checks passed")
