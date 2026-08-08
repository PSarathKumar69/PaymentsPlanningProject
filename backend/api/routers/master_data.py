"""Master Data router — Excel upload/commit/revert (Part A) and the
generic-passthrough extra-field read/edit + grid endpoints (Parts B/C) for
the data-pipeline-upload task. See backend/ingestion/upload.py,
backend/ingestion/grid.py, and backend/configuration/extra_fields.py for
the actual logic — this file is routing/plumbing only, same convention as
every other router in this codebase.

No preview/confirm step (Sarath's course-correction on the original
design): one file pick, one POST, the upload takes effect immediately.
"""
import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.configuration.extra_fields import list_extra_fields
from backend.configuration.vendor_edits import update_extra_field
from backend.db.session import SessionLocal
from backend.ingestion import column_mapping_store
from backend.ingestion.grid import build_master_grid
from backend.ingestion.upload import commit_upload, revert_upload
from backend.shared.enums import ChangeSource

from ..schemas.master_data import (
    ExtraFieldOut,
    ExtraFieldPatchRequest,
    ExtraFieldPatchResponse,
    MasterDataCommitResponse,
    MasterDataRevertResponse,
    MasterGridOut,
    SheetStartMonthOut,
)

router = APIRouter(tags=["master_data"], prefix="/master-data")


def _save_upload_to_temp(file: UploadFile) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as out:
        out.write(file.file.read())
    return tmp_path


@router.post(
    "/commit-upload",
    response_model=MasterDataCommitResponse,
    description="Takes effect immediately — backup, atomic replace, re-ingest, audit log, all in one call.",
)
def post_commit_upload(
    file: UploadFile = File(...),
    # Stretch goal (P1 demo-readiness task): blank (the default) means "use
    # whatever's currently configured" — never required, this is a
    # convenience override for the rare upload whose date range genuinely
    # shifted, not a normal part of the upload flow.
    sheet_start_month: str | None = Form(None),
    # Main-tab upload-confirm task: Finance's confirmed planning month for
    # this cycle, from the new upload confirm modal. Optional so existing
    # callers/tests that omit it keep working — commit_upload() simply
    # leaves whatever's currently stored untouched in that case.
    planning_month: str | None = Form(None),
):
    tmp_path = _save_upload_to_temp(file)
    parsed_start_month = None
    if sheet_start_month:
        try:
            parsed_start_month = datetime.strptime(sheet_start_month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="sheet_start_month must be in YYYY-MM format")
    parsed_planning_month = None
    if planning_month:
        try:
            parsed_planning_month = datetime.strptime(planning_month, "%Y-%m").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="planning_month must be in YYYY-MM format")
    try:
        return commit_upload(tmp_path, sheet_start_month=parsed_start_month, planning_month=parsed_planning_month)
    finally:
        os.remove(tmp_path)


@router.post(
    "/revert",
    response_model=MasterDataRevertResponse,
    description="Restores the one backup slot over the master file and re-ingests it.",
)
def post_revert():
    return revert_upload()


@router.get("/sheet-start-month", response_model=SheetStartMonthOut)
def get_sheet_start_month():
    """Main-tab upload-confirm task — lets the upload confirm modal pre-fill
    its "auto-detected" Sheet start month field with what's actually
    currently configured (column_mapping_store), instead of leaving it
    blank."""
    session = SessionLocal()
    try:
        return {"sheet_start_month": column_mapping_store.get_sheet_start_month(session).strftime("%Y-%m")}
    finally:
        session.close()


@router.get("/grid", response_model=MasterGridOut)
def get_master_grid():
    session = SessionLocal()
    try:
        return build_master_grid(session)
    finally:
        session.close()


@router.get("/extra-fields", response_model=list[ExtraFieldOut])
def get_extra_fields():
    session = SessionLocal()
    try:
        return list_extra_fields(session)
    finally:
        session.close()


@router.patch("/extra-fields/{vendor_id}", response_model=ExtraFieldPatchResponse)
def patch_extra_field(vendor_id: int, body: ExtraFieldPatchRequest):
    old_value, new_value = update_extra_field(
        vendor_id,
        body.column_name,
        body.new_value,
        source=ChangeSource.UI_EDIT,
        session=None,
        excel_path=body.excel_path,
    )
    return {"old_value": old_value, "new_value": new_value}
