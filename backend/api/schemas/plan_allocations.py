"""Request/response schemas for the plan-allocations router."""
from pydantic import BaseModel


class OverrideRequest(BaseModel):
    override_amount: float | None  # null clears the override, reverting to allocated_amount


class FundsWarning(BaseModel):
    """Non-blocking signal (docs/06 fix, CLAUDE.md rule 2 — suggest, never
    enforce): every vendor's committed amount on this plan_run exceeds its
    funds_figure."""

    total_committed: float
    available_funds: float
    over_by: float


class OverrideResponse(BaseModel):
    plan_allocation_id: int
    vendor_id: int
    allocated_amount: float
    override_amount: float | None
    effective_amount: float
    funds_warning: FundsWarning | None = None


class WeekDistributionRequest(BaseModel):
    # Partial update: only the weeks present here are touched, everything
    # else in the stored mapping is left as-is. Week numbers are string
    # keys (JSON object keys are always strings) — not validated against
    # 1..weeks_in_month() or against summing to any particular total; this
    # is Finance's own free-form planning note, purely additive/display-only.
    updates: dict[str, float]


class WeekDistributionResponse(BaseModel):
    plan_allocation_id: int
    vendor_id: int
    week_distribution_plan: dict[str, float]
