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

from fastapi import APIRouter, File, UploadFile

from backend.configuration.extra_fields import list_extra_fields
from backend.configuration.vendor_edits import update_extra_field
from backend.db.session import SessionLocal
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
def post_commit_upload(file: UploadFile = File(...)):
    tmp_path = _save_upload_to_temp(file)
    try:
        return commit_upload(tmp_path)
    finally:
        os.remove(tmp_path)


@router.post(
    "/revert",
    response_model=MasterDataRevertResponse,
    description="Restores the one backup slot over the master file and re-ingests it.",
)
def post_revert():
    return revert_upload()


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
