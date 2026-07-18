"""Request/response schemas for plan-run history (GET /models/{n}/plan-runs)
and the hard-delete endpoint (DELETE /plan-runs/{plan_run_id})."""
from datetime import date, datetime

from pydantic import BaseModel


class PlanRunAllocationOut(BaseModel):
    plan_allocation_id: int
    vendor_id: int
    assigned_week: int
    within_week_order: int | None
    allocated_amount: float
    # Per-row historical snapshot only (see backend/db/models.py) — NOT the
    # live value; that's vendor_week_distribution_plans on the parent
    # response, keyed by vendor_id.
    override_amount: float | None


class PlanRunOut(BaseModel):
    plan_run_id: int
    created_at: datetime
    month: date
    model_used: str
    funds_figure: float | None
    allocations: list[PlanRunAllocationOut]


class PlanRunHistoryResponse(BaseModel):
    # Oldest first (index 0 = "Plan 1"), scoped to this model family + the
    # current cycle month.
    plan_runs: list[PlanRunOut]
    # Distribution is no longer per-plan-run (this task) — this is the one
    # place the frontend reads a vendor's CURRENT (live, sticky)
    # week_distribution_plan from, keyed by vendor_id as a string. Covers
    # every vendor appearing anywhere in plan_runs above, not just the
    # latest run.
    vendor_week_distribution_plans: dict[str, dict[str, float] | None]


class DeletePlanRunResponse(BaseModel):
    plan_run_id: int
    deleted_allocations: int
