"""Vendors router — read endpoints plus the single Finance-facing edit route."""
from fastapi import APIRouter

from backend.configuration.vendor_edits import update_vendor_field
from backend.db.models import MonthlyLedger, Payment, Vendor
from backend.db.session import SessionLocal
from backend.shared.aging import compute_vendor_aging
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import ChangeSource
from backend.shared.min_funds import min_funds_tranche_breakdown, required_amount
from backend.shared.payment_logging import finalize_plan, week_actual_paid
from backend.shared.plan_history import latest_budget_for_vendor, latest_priority_for_vendor

from ..schemas.vendors import (
    FinalizePlanRequest,
    FinalizePlanResponse,
    PaymentOut,
    VendorAgingBulkItem,
    VendorAgingOut,
    VendorOut,
    VendorPatchRequest,
    VendorPatchResponse,
    VendorPaymentTrackingOut,
)

router = APIRouter(tags=["vendors"])


def _snap_to_zero(value):
    """Two independently-computed money figures that are supposed to be
    equal (e.g. budget vs actual paid) can differ by float dust well under
    a rupee — round that to a clean 0.0 rather than letting a stray
    "-0"/near-zero remainder reach the browser (Math.round() on a tiny
    negative float produces a literal negative zero, which some browsers
    render as "-0")."""
    return 0.0 if abs(value) <= MONEY_EPSILON else value


def _vendor_out(vendor: Vendor) -> dict:
    return {
        "id": vendor.id,
        "erp_code": vendor.erp_code,
        "entity": vendor.entity,
        "vendor_name": vendor.vendor_name,
        "opening_balance": float(vendor.opening_balance),
        "live_outstanding_balance": max(float(vendor.opening_balance) - float(vendor.paid_so_far_this_month), 0.0),
        "category": vendor.category.value,
        "commitment_months": vendor.commitment_months,
        "assigned_week": vendor.assigned_week,
        "payment_status": vendor.payment_status.value,
        "paid_so_far_this_month": float(vendor.paid_so_far_this_month),
        "score": float(vendor.score) if vendor.score is not None else None,
        "current_aging_bucket": vendor.current_aging_bucket,
        "reconsider": vendor.reconsider,
        # Sticky, whole-month, model-agnostic (docs/06 fix) — the frontend
        # needs this on the plain vendor record (not just per-plan-run rows)
        # so it can freeze that vendor's Reconsider toggle once it's set.
        "override_amount": float(vendor.override_amount) if vendor.override_amount is not None else None,
        "priority_tag": vendor.priority_tag,  # already a plain string (or None)
    }


@router.get("/vendors", response_model=list[VendorOut])
def list_vendors():
    session = SessionLocal()
    try:
        # is_active.isnot(False), not ==True: a vendor row that predates
        # that column (data-pipeline-upload Bug #1 fix, models.py) reads
        # NULL, which must count as active.
        vendors = session.query(Vendor).filter(Vendor.is_active.isnot(False)).order_by(Vendor.id).all()
        return [_vendor_out(v) for v in vendors]
    finally:
        session.close()


@router.post("/vendors/finalize-plan", response_model=FinalizePlanResponse)
def post_finalize_plan(body: FinalizePlanRequest = FinalizePlanRequest()):
    """"Finalize Plan" — snapshots every vendor's current effective amount
    for whichever model tab this was called for (override-or-suggestion,
    see payment_logging.py::finalize_plan) into the Vendor Payment Table's
    Budget column, so Finance can pay against a stable number across a
    daily/weekly payment round without it drifting under further
    overrides/regenerations until finalized again.

    body.model selects which model's plan_run family to snapshot (see
    finalize_plan's model_number param). Body is optional (defaults to
    model=1) so a caller sending no body at all, or an empty one, keeps
    working. New Model 2 (model 5) calls finalize_plan() directly from its
    own router instead of through this endpoint — see new_model_2.py.
    """
    session = SessionLocal()
    try:
        vendor_count = finalize_plan(session, model_number=body.model)
        session.commit()
        return {"vendor_count": vendor_count}
    finally:
        session.close()


@router.get("/vendors/payment-tracking", response_model=list[VendorPaymentTrackingOut])
def get_vendor_payment_tracking():
    """Vendor Payment Table — actual-payment tracking against whatever plan
    was finalized this cycle, distinct from the per-model planning table.

    Registered ABOVE /vendors/{vendor_id} deliberately — FastAPI matches
    routes in registration order, and a static path segment ("payment-tracking")
    would otherwise be swallowed by the dynamic {vendor_id: int} route above
    it (and fail its int coercion with a 422, never reaching this handler).

    "Outstanding" is opening_balance (this cycle's starting total owed), NOT
    the live-netted figure the planning table's own "Outstanding" column
    shows — matching that live figure here would double-subtract
    actual_paid_this_month once "Balance outstanding" (below) also
    subtracts it. "Balance outstanding" is the one that ends up numerically
    equal to the planning table's live "Outstanding" (opening_balance minus
    paid), just computed here from the real payments-table aggregation
    rather than the cached paid_so_far_this_month field.

    "Budget" is the stable snapshot taken at the last "Finalize Plan" click
    — see backend/shared/plan_history.py::latest_budget_for_vendor and
    POST /vendors/finalize-plan above. 0.0 until Finance finalizes at least
    once this cycle.

    within_week_order is also returned here (not really a "payment
    tracking" fact) purely to reuse the same per-vendor "latest plan_run,
    any model" row lookup — the planning table's Priority column merges it
    onto vendorsById via applyPaymentTracking(), the same fetch this table
    already does, rather than adding a second endpoint for one integer.

    min_funds_required is this vendor's own required_amount() figure, but
    the PRE-payment snapshot (paid_so_far_override=0.0) — not the same live
    call the Aging card/badge use. Fix (this task): required_amount() is
    live-payment-aware, so the plain live call shrinks the moment a payment
    lands (the oldest tranche it's still measuring moves forward), while
    actual_paid_this_month only ever grows — comparing a growing number
    against a shrinking one mechanically produces >100% once a vendor is
    paid past its current oldest tranche. Reconstructing the pre-payment
    figure on demand (same convention already used for the Aging card's own
    opening-vs-live split, compute_vendor_aging's already_paid_this_cycle)
    keeps the percentage meaningful without storing a second copy of the
    number anywhere.
    """
    session = SessionLocal()
    try:
        rows = []
        for vendor in session.query(Vendor).filter(Vendor.is_active.isnot(False)).order_by(Vendor.id).all():
            outstanding = float(vendor.opening_balance)
            budget = latest_budget_for_vendor(session, vendor.id)
            # Deliberately NOT vendor.paid_so_far_this_month — this table
            # tracks against the real payments table directly (week_actual_paid's
            # own aggregation), not a cached running total, even though the
            # two are always kept in lockstep by log_payment() today.
            actual_paid = sum(week_actual_paid(session, vendor.id).values())
            ledger_rows = (
                session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
            )
            min_funds_required, _ = required_amount(vendor, ledger_rows, as_of=None, paid_so_far_override=0.0)
            rows.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    "category": vendor.category.value,
                    "outstanding": outstanding,
                    "budget": budget,
                    "actual_paid_this_month": actual_paid,
                    # Two independently-sourced figures that are supposed to
                    # net to exactly zero once Finance has paid the full
                    # budgeted/outstanding amount can differ by float dust
                    # well under a rupee — snapped to a clean 0.0 (via
                    # MONEY_EPSILON, this codebase's existing money-equality
                    # tolerance) rather than displaying a stray "-0"/near-zero
                    # remainder in the browser.
                    "balance": _snap_to_zero(budget - actual_paid),  # can legitimately go negative — not clamped
                    "balance_outstanding": _snap_to_zero(max(outstanding - actual_paid, 0.0)),  # ledger integrity: never negative
                    "within_week_order": latest_priority_for_vendor(session, vendor.id),
                    "min_funds_required": min_funds_required,
                }
            )
        return rows
    finally:
        session.close()


@router.get("/vendors/aging", response_model=list[VendorAgingBulkItem])
def get_all_vendors_aging():
    """Bulk form of GET /vendors/{vendor_id}/aging — one call instead of one
    per vendor, so a page load doesn't fire ~83 simultaneous requests just
    to fill in the Planning table's Aging column (Months/Bucket/Total Min
    Funds) and the Distribution column's paid-week highlighting. Same
    per-vendor computation as the single-vendor endpoint below, just looped
    in one session/one response instead of one request each.

    Registered ABOVE /vendors/{vendor_id} deliberately — same
    static-before-dynamic-route reason documented on
    GET /vendors/payment-tracking above.
    """
    session = SessionLocal()
    try:
        results = []
        for vendor in session.query(Vendor).filter(Vendor.is_active.isnot(False)).order_by(Vendor.id).all():
            ledger_rows = (
                session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
            )
            aging = compute_vendor_aging(
                ledger_rows, as_of=None, already_paid_this_cycle=float(vendor.paid_so_far_this_month)
            )
            opening_aging = compute_vendor_aging(ledger_rows, as_of=None, already_paid_this_cycle=0.0)
            min_funds_total, min_funds_tranches = min_funds_tranche_breakdown(vendor, ledger_rows)
            results.append(
                {
                    "vendor_id": vendor.id,
                    "oldest_tranche_month": aging.oldest_tranche_month,
                    "oldest_bucket": aging.oldest_bucket,
                    "oldest_bucket_months_back": aging.oldest_bucket_months_back,
                    "bucket_balances": aging.bucket_balances,
                    "total_outstanding": aging.total_outstanding,
                    "oldest_bucket_amount": aging.oldest_bucket_amount,
                    "oldest_bucket_amount_opening": opening_aging.oldest_bucket_amount,
                    "monthly_breakdown": aging.monthly_breakdown,
                    "week_actual_paid": week_actual_paid(session, vendor.id),
                    "min_funds_required": min_funds_total,
                    "min_funds_tranches": min_funds_tranches,
                }
            )
        return results
    finally:
        session.close()


@router.get("/vendors/{vendor_id}", response_model=VendorOut)
def get_vendor(vendor_id: int):
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        return _vendor_out(vendor)
    finally:
        session.close()


@router.get("/vendors/{vendor_id}/aging", response_model=VendorAgingOut)
def get_vendor_aging(vendor_id: int):
    """Ops/debug visibility into why a vendor scored/bucketed the way it did."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        ledger_rows = (
            session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).order_by(MonthlyLedger.month).all()
        )
        aging = compute_vendor_aging(
            ledger_rows, as_of=None, already_paid_this_cycle=float(vendor.paid_so_far_this_month)
        )
        # Opening ("Old") snapshot for the vendor-payments summary table —
        # same tranches, but as they stood before any of this cycle's manual
        # payments (already_paid_this_cycle=0.0), so FIFO isolates exactly
        # how much of the oldest bill this cycle's payments cleared.
        # Additive only — the existing oldest_bucket_amount field above still
        # means "current/live", unchanged for every other caller.
        opening_aging = compute_vendor_aging(ledger_rows, as_of=None, already_paid_this_cycle=0.0)
        # docs/04 carry-forward fix: the vendor detail card's own "Min Funds
        # calculation" table (frontend only shows this before a plan exists
        # this cycle, per docs/04's two-step flow) — recomputed fresh from
        # the real ledger/payments every call, nothing stored. 0.0/[] for
        # Must Pay/Commitment (this fix is Normal-vendor-only).
        min_funds_total, min_funds_tranches = min_funds_tranche_breakdown(vendor, ledger_rows)
        return {
            "oldest_tranche_month": aging.oldest_tranche_month,
            "oldest_bucket": aging.oldest_bucket,
            "oldest_bucket_months_back": aging.oldest_bucket_months_back,
            "bucket_balances": aging.bucket_balances,
            "total_outstanding": aging.total_outstanding,
            "oldest_bucket_amount": aging.oldest_bucket_amount,
            "oldest_bucket_amount_opening": opening_aging.oldest_bucket_amount,
            "monthly_breakdown": aging.monthly_breakdown,
            "week_actual_paid": week_actual_paid(session, vendor_id),
            "min_funds_required": min_funds_total,
            "min_funds_tranches": min_funds_tranches,
        }
    finally:
        session.close()


@router.get("/vendors/{vendor_id}/payments", response_model=list[PaymentOut])
def get_vendor_payments(vendor_id: int):
    session = SessionLocal()
    try:
        payments = (
            session.query(Payment).filter_by(vendor_id=vendor_id).order_by(Payment.payment_date).all()
        )
        return [
            {"id": p.id, "vendor_id": p.vendor_id, "payment_date": p.payment_date, "amount": float(p.amount), "week": p.week, "note": p.note}
            for p in payments
        ]
    finally:
        session.close()


@router.patch("/vendors/{vendor_id}", response_model=VendorPatchResponse)
def patch_vendor(vendor_id: int, body: VendorPatchRequest):
    old_value, new_value = update_vendor_field(
        vendor_id, body.field, body.new_value, source=ChangeSource.UI_EDIT, session=None, excel_path=body.excel_path
    )
    to_plain = lambda v: v.value if hasattr(v, "value") else v
    return {"old_value": to_plain(old_value), "new_value": to_plain(new_value)}
