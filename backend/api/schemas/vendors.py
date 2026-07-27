"""Request/response schemas for the vendors router."""
from datetime import date
from typing import Any

from pydantic import BaseModel


class VendorOut(BaseModel):
    id: int
    erp_code: str
    entity: str
    vendor_name: str
    opening_balance: float
    live_outstanding_balance: float  # opening_balance minus this cycle's live payments, clamped at 0
    category: str
    commitment_months: int | None
    assigned_week: int | None
    payment_status: str
    paid_so_far_this_month: float
    score: float | None
    current_aging_bucket: str | None
    reconsider: bool | None
    override_amount: float | None
    priority_tag: str | None  # New Model 2's Finance-assigned P2/P3/P4 tag (docs/14-new-model-2.md)


class VendorAgingOut(BaseModel):
    oldest_tranche_month: date | None
    oldest_bucket: str | None
    oldest_bucket_months_back: int | None  # real months-back, e.g. 7 or 12 for a "120+" vendor — not capped at 4
    bucket_balances: dict[str, float]
    total_outstanding: float
    oldest_bucket_amount: float  # docs/04: just the single oldest tranche, not bucket_balances' lumped sum
    # Same oldest-tranche amount, but before this cycle's manual payments
    # were applied (already_paid_this_cycle=0.0) — the "Old" half of the
    # vendor-payments summary table's Old/New pair. oldest_bucket_amount
    # above stays "current/live", unchanged in meaning.
    oldest_bucket_amount_opening: float
    monthly_breakdown: list[dict[str, Any]]  # oldest -> newest, one entry per calendar month incl. zero-balance ones
    # Live, never stored redundantly (same figure build_weekly_view() already
    # exposes at plan-generation time) — included here too so the UI's
    # existing per-vendor refresh-after-payment call (this same endpoint)
    # can update the Distribution column's "Paid" badges without needing a
    # full plan regeneration, which would also reset week_distribution_plan.
    week_actual_paid: dict[str, float]
    # docs/04 carry-forward fix — the vendor detail card's "Min Funds
    # calculation" table. Normal-vendor-only (0.0/[] for Must Pay/
    # Commitment, which don't have a tranche-based required amount at all).
    # Recomputed fresh from the real ledger/payments every call — nothing
    # stored (Option A, confirmed). See
    # backend/shared/min_funds.py::min_funds_tranche_breakdown.
    min_funds_required: float
    min_funds_tranches: list[dict[str, Any]]  # oldest -> newest, {month, remaining_amount, is_leftover}


class VendorAgingBulkItem(VendorAgingOut):
    """Same shape as VendorAgingOut, plus vendor_id — GET /vendors/aging
    (bulk) returns a list of these so the frontend can populate the
    Planning table's Aging column with one request instead of one
    GET /vendors/{id}/aging per vendor."""

    vendor_id: int


class PaymentOut(BaseModel):
    id: int
    vendor_id: int
    payment_date: date
    amount: float
    week: int | None
    note: str | None = None


class VendorPatchRequest(BaseModel):
    field: str
    new_value: Any
    excel_path: str | None = None  # override for tests; production calls omit it (writes the real master sheet)


class VendorPatchResponse(BaseModel):
    old_value: Any
    new_value: Any


class VendorPaymentTrackingOut(BaseModel):
    """One row of the Vendor Payment Table (actual-payment tracking,
    distinct from the per-model planning table) — see
    backend/shared/plan_history.py::latest_budget_for_vendor for the
    "budget" sourcing (a stable snapshot from the last "Finalize Plan"
    click, POST /vendors/finalize-plan)."""

    vendor_id: int
    erp_code: str
    vendor_name: str
    category: str
    outstanding: float  # opening_balance — this cycle's starting total owed, NOT netted against actual_paid_this_month
    budget: float  # stable snapshot from the last "Finalize Plan" click — see plan_history.py::latest_budget_for_vendor
    actual_paid_this_month: float  # summed from the real payments table (week_actual_paid), not the cached running total
    balance: float  # budget - actual_paid_this_month; CAN go negative (paid more than budgeted) — not an error
    # Reused by the planning table's Priority column (Wn-Pm) so Priority
    # reflects whichever model was actually used latest, not whichever tab
    # is active — see plan_history.py::latest_priority_for_vendor. A live
    # "latest plan_run, any model" lookup, unlike Budget above (which is a
    # frozen Finalize snapshot) — this one still always reflects the truth.
    within_week_order: int | None
    balance_outstanding: float  # outstanding - actual_paid_this_month, clamped at 0 — ledger integrity, must never go negative
    # This vendor's own required_amount() figure, but the PRE-payment
    # snapshot (paid_so_far_override=0.0) — this cycle's payments already
    # moved the live version, so this is reconstructed as it stood before
    # any of them landed (see the router's own docstring). NOT tied to
    # Budget/override. Frontend's "% Min Funds Paid" = actual_paid_this_month
    # / this figure.
    min_funds_required: float


class FinalizePlanRequest(BaseModel):
    # Which model's plan_run family to snapshot into Budget. New Model 2
    # (model 5) calls finalize_plan() directly from its own router instead
    # of through this endpoint. Defaults to 1 for any caller sending an
    # empty body.
    model: int = 1


class FinalizePlanResponse(BaseModel):
    vendor_count: int  # number of vendors whose Budget snapshot was (re)written
