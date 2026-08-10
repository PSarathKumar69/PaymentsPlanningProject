"""API-layer tests for the master-data router (data-pipeline-upload task).

Deep diff/widget/atomic-write logic is already covered at the function
level in backend/ingestion/test_upload.py and
backend/configuration/test_extra_fields.py — these only confirm each route
maps request -> underlying function -> response JSON correctly (same scope
note as backend/api/test_api.py's own docstring), using that same file's
isolation convention: fresh temp DB + a tmp_path copy of the real master
Excel, real data/Vendor's Details.xlsx and backend/db/app.db never touched.
"""
import os
import re
import shutil
import sys

import pytest
from fastapi.testclient import TestClient


def _fresh_app(tmp_path):
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]
    from backend.api.main import app

    return app


@pytest.fixture
def client(tmp_path):
    return TestClient(_fresh_app(tmp_path))


@pytest.fixture
def excel_copy(tmp_path):
    from backend.ingestion.load_excel import EXCEL_PATH

    dest = tmp_path / "copy.xlsx"
    shutil.copy(EXCEL_PATH, dest)
    return str(dest)


@pytest.fixture
def seeded_client(client, excel_copy):
    resp = client.post("/ingestion/load", json={"excel_path": excel_copy})
    assert resp.status_code == 200
    # Vercel-migration task: /ingestion/load with an explicit excel_path
    # (this fixture's whole point) never touches the DB-backed master
    # workbook blob — but /master-data/grid's own default (no override)
    # now reads from that blob, not a file, so tests exercising the real
    # grid endpoint need it seeded directly.
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import set_workbook_bytes

    session = SessionLocal()
    with open(excel_copy, "rb") as f:
        set_workbook_bytes(session, "current", f.read())
    session.commit()
    session.close()
    return client


def test_grid_endpoint_returns_no_data_shape_when_nothing_ingested_yet(client):
    """Genuinely fresh environment — no /ingestion/load, no upload, ever.
    Must never touch the real master file at all (vendor-count check
    short-circuits first) and never 500."""
    resp = client.get("/master-data/grid")
    assert resp.status_code == 200
    assert resp.json() == {
        "columns": [], "vendors": [], "extra_field_widgets": {}, "has_data": False,
        "duplicate_erp_codes": [], "duplicate_dropped_rows": [],
    }


def test_grid_endpoint_returns_no_data_shape_when_master_file_is_missing(seeded_client):
    """Backstop case: vendor rows already exist (a prior upload succeeded),
    but the master workbook blob itself has since gone missing — same
    clean empty shape, not a raw 500."""
    from backend.db.models import MasterWorkbook
    from backend.db.session import SessionLocal

    session = SessionLocal()
    session.query(MasterWorkbook).filter_by(slot="current").delete()
    session.commit()
    session.close()

    resp = seeded_client.get("/master-data/grid")
    assert resp.status_code == 200
    assert resp.json() == {
        "columns": [], "vendors": [], "extra_field_widgets": {}, "has_data": False,
        "duplicate_erp_codes": [], "duplicate_dropped_rows": [],
    }


def test_grid_endpoint_returns_columns_in_file_order(seeded_client):
    resp = seeded_client.get("/master-data/grid")
    assert resp.status_code == 200
    body = resp.json()
    headers = [c["header"] for c in body["columns"]]
    assert headers[0] == "ERP Code"  # first real column, confirms file-order (not alphabetical/arbitrary)
    assert len(body["vendors"]) == 83
    assert body["vendors"][0]["erp_code"]


def test_grid_endpoint_formats_datetime_typed_header_cells(seeded_client):
    """Master Grid bug: the real sheet has a genuine mix of plain-string and
    literal-datetime header cells for its month columns (column_mapping.py's
    module docstring) — confirmed real ones sit in the payable/payment
    blocks. Before the fix, a datetime-typed cell rendered as str()'s raw
    "2026-04-26 00:00:00" instead of a sensible label. No raw datetime
    string should ever reach the grid response, and at least one payable
    header (real data guarantees a mix) must show the formatted "Mon-YY"
    style — proves the fix against the real file, not a synthetic one."""
    resp = seeded_client.get("/master-data/grid")
    assert resp.status_code == 200
    body = resp.json()

    raw_datetime_pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    for col in body["columns"]:
        assert not raw_datetime_pattern.match(col["header"]), f"unformatted datetime header: {col['header']!r}"

    payable_headers = [c["header"] for c in body["columns"] if c["kind"] == "payable"]
    assert payable_headers
    month_year_pattern = re.compile(r"^[A-Za-z]{3}-\d{2}$")
    assert any(month_year_pattern.match(h) for h in payable_headers)


def test_grid_endpoint_flags_duplicate_erp_codes_and_lists_dropped_rows(client, tmp_path):
    """Go-live "show every row" task: a sheet with a duplicate ERP code
    (unrelated vendors sharing a code by data-entry error, same fixture
    shape as test_load_excel.py's own duplicate-detection test) must have
    the winning vendor flagged in duplicate_erp_codes and the later,
    skipped row(s) traceable via duplicate_dropped_rows — first-row-wins
    (row 2/"Alpha Freight Services", per Sarath's later call reversing the
    original last-row-wins decision)."""
    import openpyxl

    from backend.ingestion.column_mapping import build_sheet_map

    fixtures_dir = os.path.join(
        os.path.dirname(__file__), "..", "ingestion", "test_fixtures"
    )
    path = str(tmp_path / "dup.xlsx")
    shutil.copy(os.path.join(fixtures_dir, "single_header_row_sheet.xlsx"), path)

    wb = openpyxl.load_workbook(path)
    ws = wb.active
    sheet_map = build_sheet_map(ws)
    ws.cell(row=3, column=sheet_map.erp_code_col, value="VT0001")
    wb.save(path)

    resp = client.post("/ingestion/load", json={"excel_path": path})
    assert resp.status_code == 200

    # build_master_grid() defaults to reading the DB-backed "current" blob
    # for column identity/order — seed it with the same file we just
    # ingested so the duplicate scan lines up with what's actually in the
    # DB (same reasoning as seeded_client's own fixture in this file).
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import set_workbook_bytes

    session = SessionLocal()
    with open(path, "rb") as f:
        set_workbook_bytes(session, "current", f.read())
    session.commit()
    session.close()

    body = client.get("/master-data/grid").json()
    assert body["duplicate_erp_codes"] == ["VT0001"]
    assert body["duplicate_dropped_rows"] == [
        {"erp_code": "VT0001", "vendor_name": "Bravo Industrial Supplies", "row": 3}
    ]

    # First-row-wins: exactly one live vendor for VT0001, and it's the
    # earlier row's own name.
    winners = [v for v in body["vendors"] if v["erp_code"] == "VT0001"]
    assert len(winners) == 1
    assert winners[0]["values"]["Vendor Name"] == "Alpha Freight Services"


def test_extra_fields_endpoint_lists_real_unmapped_columns(seeded_client):
    resp = seeded_client.get("/master-data/extra-fields")
    assert resp.status_code == 200
    columns = {f["column_name"] for f in resp.json()}
    assert {"Unique", "VN"} <= columns  # the real sheet's own genuinely-unmapped columns


def test_patch_extra_field_endpoint_writes_db_and_audit_log(seeded_client, tmp_path):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    excel_copy_2 = str(tmp_path / "edit_copy.xlsx")
    from backend.ingestion.load_excel import EXCEL_PATH

    # patch_extra_field's excel_path override writes to a throwaway copy —
    # confirms the route wires body.excel_path through, not a real-file check.
    shutil.copy(EXCEL_PATH, excel_copy_2)

    resp = seeded_client.patch(
        f"/master-data/extra-fields/{vendor_id}",
        json={"column_name": "Unique", "new_value": "test-value", "excel_path": excel_copy_2},
    )
    assert resp.status_code == 200
    assert resp.json()["new_value"] == "test-value"

    resp = seeded_client.get("/audit-log", params={"vendor_id": vendor_id})
    assert resp.status_code == 200
    assert any(e["field_name"] == "Unique" and e["new_value"] == "test-value" for e in resp.json()["items"])


def test_commit_upload_endpoint_then_revert_endpoint(seeded_client, excel_copy):
    """Full round trip through the real HTTP routes: commit an upload, then
    revert it — both use the DB-backed master workbook blob by default
    (no override at the API layer, unlike the function-level tests), which
    lives inside this test's own isolated temp DB (PAYMENTS_DB_PATH), so
    this never touches the real master file."""
    with open(excel_copy, "rb") as f:
        resp = seeded_client.post(
            "/master-data/commit-upload",
            files={"file": ("upload.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200
    assert resp.json()["vendors_changed"] >= 0
    # Schema-gap fix (P1 demo-readiness task): MasterDataCommitResponse used
    # to omit this key entirely, so FastAPI's response_model silently
    # dropped it — the AI column-mapping/sheet-start-month warning banners
    # never actually reached the frontend through the real HTTP endpoint,
    # only when a test called commit_upload() directly in-process.
    assert "ai_column_mapping_messages" in resp.json()

    from backend.db.models import MasterWorkbook
    from backend.db.session import SessionLocal

    session = SessionLocal()
    assert session.query(MasterWorkbook).filter_by(slot="backup").first() is not None
    session.close()

    resp = seeded_client.post("/master-data/revert")
    assert resp.status_code == 200
    assert "not covered by this revert" in resp.json()["warning"]


def test_sheet_start_month_endpoint_returns_configured_value(seeded_client):
    resp = seeded_client.get("/master-data/sheet-start-month")
    assert resp.status_code == 200
    assert resp.json()["sheet_start_month"]  # seeded default, never blank


def test_commit_upload_persists_planning_month(seeded_client, excel_copy):
    """Main-tab upload-confirm task (section 3): commit-upload's new optional
    planning_month field is readable back through
    GET /models/5/current-planning-month before any plan_run exists."""
    with open(excel_copy, "rb") as f:
        resp = seeded_client.post(
            "/master-data/commit-upload",
            data={"planning_month": "2026-09"},
            files={"file": ("upload.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200

    resp = seeded_client.get("/models/5/current-planning-month")
    assert resp.status_code == 200
    assert resp.json()["planning_month"] == "2026-09"


def test_commit_upload_planning_month_bad_format_is_400(seeded_client, excel_copy):
    with open(excel_copy, "rb") as f:
        resp = seeded_client.post(
            "/master-data/commit-upload",
            data={"planning_month": "not-a-month"},
            files={"file": ("upload.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 400


def test_revert_endpoint_without_a_prior_upload_is_400(seeded_client):
    # seeded_client only ever ran /ingestion/load (never /master-data/commit-
    # upload), so the DB-backed "backup" slot was never populated — no
    # monkeypatching needed, this is genuinely the "no backup yet" state.
    resp = seeded_client.post("/master-data/revert")
    assert resp.status_code == 400
    assert "no backup available" in resp.json()["detail"]
