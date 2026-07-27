"""Request/response schemas for the weekly-view and regeneration routers."""
from typing import Any

from pydantic import BaseModel


class WeeklyViewRequest(BaseModel):
    vendor_allocations: list[dict[str, Any]]  # a prior generate_plan()'s "allocations" records
    model_used: str | None = None
    funds_figure: float | None = None
    persist: bool = True


class WeeklyViewResponse(BaseModel):
    weekly_summary: list[dict[str, Any]]
    detail: list[dict[str, Any]]
    plan_run_id: int | None


