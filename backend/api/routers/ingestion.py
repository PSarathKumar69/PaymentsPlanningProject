"""Ingestion router — ops/admin only. Full re-ingestion of the master Excel;
safely re-runnable (fix round 4), but not part of Finance's day-to-day flow."""
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.ingestion.load_excel import load

router = APIRouter(tags=["ingestion"])


class IngestionLoadRequest(BaseModel):
    excel_path: str | None = None


@router.post(
    "/ingestion/load",
    response_model=dict[str, Any],
    description="Ops/admin action: full re-ingestion of the master Excel. Safely re-runnable, "
    "but not part of Finance's day-to-day flow. excel_path is an explicit override (tests/ops); "
    "omitted, this re-ingests the DB-backed 'current' master workbook.",
)
def post_ingestion_load(body: IngestionLoadRequest):
    return load(excel_path=body.excel_path, session=None)
