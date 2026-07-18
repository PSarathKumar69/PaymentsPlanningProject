"""Rollover router — ops/deliberate action, same framing as ingestion."""
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from backend.ingestion.load_excel import EXCEL_PATH
from backend.month_end.rollover import rollover_month

router = APIRouter(tags=["rollover"])


class RolloverRequest(BaseModel):
    excel_path: str | None = None


@router.post(
    "/rollover",
    response_model=dict[str, Any],
    description="Ops/deliberate action: month-end rollover. Re-ingests the Excel and, only on a "
    "genuine new month, resets every vendor's payment-cycle state.",
)
def post_rollover(body: RolloverRequest):
    result = rollover_month(excel_path=body.excel_path or EXCEL_PATH, session=None)
    return result
