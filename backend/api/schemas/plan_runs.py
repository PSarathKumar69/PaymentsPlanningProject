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
    # Frozen required_amount/denominator this row's allocated_amount was
    # computed against, at creation time (this task's fix) — lets the
    # frontend compute a historical Suggested %/Override % for every
    # plan_run, not just the latest one. None for the rare ingestion-seeded
    # row (see backend/db/models.py).
    required_amount_snapshot: float | None


class PlanRunOut(BaseModel):
    plan_run_id: int
    created_at: datetime
    month: date
    model_used: str
    funds_figure: float | None
    # Funds Left card fix: both were silently dropped by response_model
    # before this — same undeclared-key bug class as ai_column_mapping_messages
    # and leftover_topup_total. NULL on rows that predate these columns.
    min_funds_required: float | None
    leftover_remaining: float | None
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
    # Vendors whose override_amount got cleared as a side effect of this
    # delete (bug fix, this task) — only when the deleted plan_run was the
    # latest for its model family; see the router's own docstring.
    cleared_override_vendor_ids: list[int] = []
