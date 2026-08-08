"""Part B regression test (P1 demo-readiness task): a persisted AI-resolved
header override for a REQUIRED field must be honored not just by load(),
but also by the Master Data grid (grid.py::build_master_grid) and a vendor
field edit's Excel write-back (vendor_edits.py::update_vendor_field) — both
used to build their own sheet map without header_overrides at all, so a
sheet needing an override for a required field (erp_code/entity/
vendor_name/opening_balance/total_payable/total_payment) ingested fine but
then crashed with MissingRequiredColumnsError the moment Finance opened the
grid or edited a vendor's category/tag. Same isolation convention as
test_priority_tag_mapping.py — dataset-agnostic, renames whatever "ERP Code"
resolves to on the currently-loaded real master sheet, never hardcodes a
specific vendor/row count.
"""
import os
import shutil
import sys

import openpyxl


def _fresh_db_and_master(tmp_path):
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
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


def test_grid_load_and_vendor_edit_succeed_when_a_required_header_is_ai_resolved(tmp_path):
    master_path = _fresh_db_and_master(tmp_path)

    from backend.ingestion.column_mapping import find_column, resolve_header_row

    # Simulate a real sheet that renamed the ERP Code column — the fixed
    # default ("ERP Code") no longer resolves at all, so build_sheet_map()
    # needs the persisted override for every one of its own call sites, not
    # just load()'s.
    wb = openpyxl.load_workbook(master_path)
    ws = wb.active
    erp_code_col = find_column(ws, "ERP Code")
    assert erp_code_col is not None, "fixture assumption: real sheet still uses the default ERP Code header"
    header_row, _ = resolve_header_row(ws)
    ws.cell(row=header_row, column=erp_code_col, value="Vendor Code")
    renamed_path = str(tmp_path / "renamed.xlsx")
    wb.save(renamed_path)

    from backend.db.models import Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion import column_mapping_store
    from backend.ingestion.load_excel import load

    session = SessionLocal()
    try:
        # Persisted as if the AI mapper had already resolved this once
        # (ai_column_mapper.py's own save_override call, skipped here since
        # this test only needs to prove the DOWNSTREAM threading, not the
        # Gemini call itself).
        column_mapping_store.save_override(session, "erp_code", "Vendor Code", set_by="ai")
        session.commit()

        header_overrides = column_mapping_store.load_overrides(session)
        report = load(excel_path=renamed_path, session=session, header_overrides=header_overrides)
        session.commit()
        assert report["vendor_count"] > 0

        vendor_id = session.query(Vendor).order_by(Vendor.id).first().id
    finally:
        session.close()

    # Bug (pre-fix): build_master_grid() built its own sheet map with no
    # header_overrides -> MissingRequiredColumnsError, even though ingestion
    # itself worked fine.
    session = SessionLocal()
    try:
        from backend.ingestion.grid import build_master_grid

        grid = build_master_grid(session, excel_path=renamed_path)
        assert grid["vendors"]
        assert any(c["kind"] == "erp_code" and c["header"] == "Vendor Code" for c in grid["columns"])
    finally:
        session.close()

    # Bug (pre-fix): update_vendor_field()'s Excel write-back staged via a
    # sheet map with no header_overrides either -> same crash the moment
    # Finance edited any vendor field on this sheet.
    from backend.configuration.vendor_edits import update_vendor_field

    old_value, new_value = update_vendor_field(vendor_id, "commitment_months", 3, excel_path=renamed_path)
    assert new_value == 3

    session = SessionLocal()
    try:
        assert session.query(Vendor).filter_by(id=vendor_id).one().commitment_months == 3
    finally:
        session.close()


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    test_grid_load_and_vendor_edit_succeed_when_a_required_header_is_ai_resolved(Path(tempfile.mkdtemp()))
    print("OK")
