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
    return client


def test_grid_endpoint_returns_columns_in_file_order(seeded_client):
    resp = seeded_client.get("/master-data/grid")
    assert resp.status_code == 200
    body = resp.json()
    headers = [c["header"] for c in body["columns"]]
    assert headers[0] == "ERP Code"  # first real column, confirms file-order (not alphabetical/arbitrary)
    assert len(body["vendors"]) == 83
    assert body["vendors"][0]["erp_code"]


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
    assert any(e["field_name"] == "Unique" and e["new_value"] == "test-value" for e in resp.json())


def test_commit_upload_endpoint_then_revert_endpoint(seeded_client, excel_copy, tmp_path, monkeypatch):
    """Full round trip through the real HTTP routes: commit an upload, then
    revert it — both use the real EXCEL_PATH/backup-path module constants
    (no override at the API layer, unlike the function-level tests), so
    this points those constants at tmp_path copies via monkeypatch rather
    than ever touching the real master file."""
    import backend.ingestion.upload as upload_module

    fake_master = str(tmp_path / "master.xlsx")
    shutil.copy(excel_copy, fake_master)
    fake_backup = str(tmp_path / "master.backup.xlsx")
    monkeypatch.setattr(upload_module, "EXCEL_PATH", fake_master)
    monkeypatch.setattr(upload_module, "MASTER_EXCEL_BACKUP_PATH", fake_backup)

    with open(excel_copy, "rb") as f:
        resp = seeded_client.post(
            "/master-data/commit-upload",
            files={"file": ("upload.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200
    assert resp.json()["vendors_changed"] >= 0
    assert os.path.exists(fake_backup)

    resp = seeded_client.post("/master-data/revert")
    assert resp.status_code == 200
    assert "not covered by this revert" in resp.json()["warning"]


def test_revert_endpoint_without_a_prior_upload_is_400(seeded_client, tmp_path, monkeypatch):
    import backend.ingestion.upload as upload_module

    monkeypatch.setattr(upload_module, "EXCEL_PATH", str(tmp_path / "master.xlsx"))
    monkeypatch.setattr(upload_module, "MASTER_EXCEL_BACKUP_PATH", str(tmp_path / "nonexistent.backup.xlsx"))

    resp = seeded_client.post("/master-data/revert")
    assert resp.status_code == 400
    assert "no backup available" in resp.json()["detail"]
