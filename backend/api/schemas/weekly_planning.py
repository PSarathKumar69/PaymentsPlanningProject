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


class GeneratePlanAndWeeklyViewResponse(BaseModel):
    plan: dict[str, Any]
    weekly_view: WeeklyViewResponse
    # Model 1 only (docs/06's override-exclusion fix) — 0.0 for Models 2/3
    # and for Model 1 itself whenever nothing's currently overridden.
    total_overridden: float = 0.0
    funds_left_for_regeneration: float = 0.0


class RegenerateRequest(BaseModel):
    model: int  # 1, 2, or 3 — selects which model's generate_plan is used
    available_funds: float
    current_week: int
    reconsider_decisions: dict[int, bool] | None = None


class RegenerateResponse(BaseModel):
    plan_run_id: int
    allocations: list[dict[str, Any]]
    excluded: dict[int, str]
    pulled_forward: list[int]
    unallocated_surplus: float
