"""Request/response schemas for the New Model 2 router (docs/14-new-model-2.md)."""
from datetime import date
from typing import Any

from pydantic import BaseModel

from .weekly_planning import WeeklyViewResponse


class NewModel2GeneratePlanRequest(BaseModel):
    """docs/14 "Planning month (as_of)" — planning_month is "YYYY-MM" (month
    + year, no day). Required on the first Generate Plan of a cycle;
    optional on every regeneration after that (the stored value on this
    cycle's plan_run is reused instead — see
    backend/api/routers/new_model_2.py's _resolve_planning_month())."""

    available_funds: float
    planning_month: str | None = None


class NewModel2MinimumFundsRequiredResponse(BaseModel):
    """GET /models/5/minimum-funds-required — docs/14's Card 2, using the
    new required_amount_v2 rule (backend/shared/min_funds_v2.py), scoped to
    Must Pay/Normal/Inactive/Commitment alike."""

    total: float
    breakdown: list[dict[str, Any]]
    planning_month: str  # "YYYY-MM", echoed back from the request
    as_of: date  # planning_month minus one calendar month — the aging reference point actually used
    # Non-blocking (this task): set when `planning_month` isn't exactly the
    # sheet's last recorded ledger month + 1 — never blocks Card 1/2, just
    # flags it plainly. None when it matches (the common case) or when
    # there's no ledger data yet to compare against.
    planning_month_warning: str | None = None


class SuggestedPlanningMonthResponse(BaseModel):
    """GET /models/5/suggested-planning-month (this task) — the sheet's real
    last recorded ledger month, and the suggested planning month (that + 1),
    for the frontend to pre-fill Card 1's picker instead of leaving it
    blank/defaulting to today's real calendar month. Both None when the DB
    has no ledger data loaded at all yet."""

    last_recorded_month: str | None = None
    suggested_planning_month: str | None = None


class CurrentPlanningMonthResponse(BaseModel):
    """GET /models/5/current-planning-month (Main-tab upload-confirm task) —
    whatever _resolve_planning_month() would fall back to right now: the
    latest plan_run's month if one exists this cycle, else the persisted
    planning.current_planning_month config value, else None."""

    planning_month: str | None = None


class AllVendorMinFundsRequiredV2Response(BaseModel):
    """GET /models/5/vendor-min-funds-required — bulk per-vendor v2 totals
    for New Model 2's own planning table "Total Min Funds" column (fix:
    that column previously always read the OLD carry-forward rule from
    GET /vendors/{id}/aging, same as every other tab, never New Model 2's
    own required_amount_v2). planning_month/as_of are None when no New
    Model 2 plan_run exists yet this cycle and none was passed explicitly —
    breakdown is then empty, and the frontend shows "—" rather than a
    wrong/old number."""

    planning_month: str | None = None
    as_of: date | None = None
    breakdown: list[dict[str, Any]]


class VendorMinFundsRequiredV2Response(BaseModel):
    """GET /models/5/vendors/{vendor_id}/min-funds-required — the vendor
    detail card's new "Min Funds Required" section (docs/14). Must Pay/
    Normal/Inactive populate current_*/oldest_* (Commitment fields stay None);
    Commitment populates opening_balance/live_balance/commitment_months
    (current_*/oldest_* stay None) — the frontend picks its template
    sentence off `rule` and fills in whichever fields are populated.

    second_month/second_amount (this task's fix): the actual second month
    picked when rule is v2_oldest_and_current/v2_oldest_and_second — NOT
    always the same as current_month. Only populated for those two rules;
    None everywhere else (including v2_oldest_only, where there's no second
    month at all)."""

    total: float
    rule: str
    category: str
    current_month: date | None = None
    current_amount: float | None = None
    oldest_month: date | None = None
    oldest_amount: float | None = None
    second_month: date | None = None
    second_amount: float | None = None
    opening_balance: float | None = None
    live_balance: float | None = None
    commitment_months: int | None = None
    planning_month: str
    as_of: date


class NewModel2GeneratePlanResponse(BaseModel):
    available_funds: float
    guaranteed_total: float
    escalation: bool
    escalation_shortfall: float
    bucket_ceiling: dict[str, float]  # {"P2": 0.95, "P3": 0.90, "P4": 0.85} — config-sourced, docs/14
    bucket_pct: dict[str, float]  # the pct actually used per bucket, after any cutting
    exceptional_shortfall: bool
    # Bug fix (this task): allocator.py's generate_plan() has always
    # returned this (the real, post-allocation "money left over" figure),
    # but this schema never declared it, so FastAPI's response_model
    # silently stripped it before it ever reached the frontend — same
    # failure mode as the earlier ai_column_mapping_messages gap. The
    # "Funds Left" UI card needs this one, not funds_left_for_regeneration
    # (a different, pre-allocation figure — see PlanningView.tsx).
    leftover_remaining: float
    # Bug fix (New Model 2 e2e validation task, 2026-08-03): same silent-strip
    # failure mode as leftover_remaining/ai_column_mapping_messages above —
    # allocator.py's generate_plan() has always returned this (how much of
    # leftover_remaining's pre-top-up figure the top-up sweep actually spent),
    # but this schema never declared it either, so it never reached the API
    # response despite being computed correctly the whole time.
    leftover_topup_total: float
    allocations: list[dict[str, Any]]


class NewModel2PlanAndWeeklyViewResponse(BaseModel):
    plan: NewModel2GeneratePlanResponse
    weekly_view: WeeklyViewResponse
    total_overridden: float = 0.0
    funds_left_for_regeneration: float = 0.0


class ResponsibleVendor(BaseModel):
    vendor_id: int
    erp_code: str
    vendor_name: str
    suggested_amount: float
    override_amount: float


class ResetCycleResponse(BaseModel):
    """POST /models/5/reset-cycle — confirmed-with-Sarath scope (CLAUDE.md):
    wipes only the current planning cycle's own Generate Plan output plus
    every vendor's whole-month cycle state. Never touches vendor master
    data/Excel, Configuration, past months' plan history, or the confirmed
    planning month itself."""

    planning_month: str
    deleted_plan_runs: int
    deleted_allocations: int
    vendors_reset: int


class FinalizeCheckResponse(BaseModel):
    """POST /models/5/finalize — docs/14's three-option block, mirrors New
    Model 1's FinalizeCheckResponse exactly (see
    backend/api/schemas/new_model_1.py's own docstring — not re-explained
    here)."""

    ok: bool
    total_committed: float
    available_funds: float
    over_by: float
    responsible_vendors: list[ResponsibleVendor]
    vendor_count: int = 0
