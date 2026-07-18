"""Standalone weekly-view router — wraps build_weekly_view() directly for
flexibility/testing, independent of the per-model convenience endpoints."""
import pandas as pd
from fastapi import APIRouter

from backend.weekly_planning.planner import build_weekly_view

from ..schemas.weekly_planning import WeeklyViewRequest, WeeklyViewResponse

router = APIRouter(tags=["weekly_planning"])


@router.post("/weekly-view", response_model=WeeklyViewResponse)
def post_weekly_view(body: WeeklyViewRequest):
    vendor_allocations = pd.DataFrame(body.vendor_allocations)
    result = build_weekly_view(
        vendor_allocations,
        session=None,
        as_of=None,
        model_used=body.model_used,
        funds_figure=body.funds_figure,
        persist=body.persist,
    )
    return {
        "weekly_summary": result["weekly_summary"].reset_index().to_dict(orient="records"),
        "detail": result["detail"].to_dict(orient="records"),
        "plan_run_id": result["plan_run_id"],
    }
