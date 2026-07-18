"""Ingestion router — ops/admin only. Full re-ingestion of the master Excel;
safely re-runnable (fix round 4), but not part of Finance's day-to-day flow."""
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.ingestion.load_excel import EXCEL_PATH, load

router = APIRouter(tags=["ingestion"])


class IngestionLoadRequest(BaseModel):
    excel_path: str | None = None


@router.post(
    "/ingestion/load",
    response_model=dict[str, Any],
    description="Ops/admin action: full re-ingestion of the master Excel. Safely re-runnable, "
    "but not part of Finance's day-to-day flow.",
)
def post_ingestion_load(body: IngestionLoadRequest):
    return load(excel_path=body.excel_path or EXCEL_PATH, session=None)
