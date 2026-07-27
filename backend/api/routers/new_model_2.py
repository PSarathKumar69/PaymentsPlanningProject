"""New Model 2 router — priority-bucket ceiling allocation (docs/14-new-model-2.md).

Additive alongside Models 1/2/3 and New Model 1 (this build's own
guardrail — see CLAUDE.md: none of those get touched). Mirrors
backend/api/routers/new_model_1.py almost exactly — same override/locking
mechanism (an overridden vendor is excluded from this round's computation
via eligible_vendor_ids, and their override amount is subtracted from
available_funds up front), same frozen-Suggested-figure-on-lock pattern.
See new_model_1.py's own docstrings for the full rationale; not
re-explained here.

Planning month (docs/14, new): New Model 2 needs Finance's own explicitly-
picked planning month (month + year, no day) before anything else — never
_current_cycle_month() (backend/shared/payment_logging.py), the ledger-
ingestion-anchored value every other model still uses unchanged. See
_resolve_planning_month()/_month_minus_one() below.
"""
from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException

from backend.db.models import MonthlyLedger, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.models.new_model_2.allocator import generate_plan
from backend.shared.constants import MONEY_EPSILON, NEW_MODEL_2_PLAN_RUN_LABEL
from backend.shared.enums import AllocationStatus
from backend.shared.min_funds import live_outstanding_balance
from backend.shared.min_funds_v2 import calculate_minimum_funds_required_v2, min_funds_breakdown_v2, required_amount_v2
from backend.shared.payment_logging import finalize_plan
from backend.shared.plan_history import build_plan_run_history_response
from backend.weekly_planning.planner import build_weekly_view

from ..schemas.new_model_2 import (
    AllVendorMinFundsRequiredV2Response,
    FinalizeCheckResponse,
    NewModel2GeneratePlanRequest,
    NewModel2MinimumFundsRequiredResponse,
    NewModel2PlanAndWeeklyViewResponse,
    VendorMinFundsRequiredV2Response,
)
from ..schemas.plan_runs import PlanRunHistoryResponse

router = APIRouter(tags=["new_model_2"])


def _plan_response(result: dict) -> dict:
    return {**result, "allocations": result["allocations"].to_dict(orient="records")}


def _parse_planning_month(value: str) -> date:
    """"YYYY-MM" (docs/14: month + year, no day) -> the first-of-month date
    PlanRun.month/aging as_of both use. Raises ValueError on a bad format —
    callers turn that into an HTTP 400."""
    try:
        year_str, month_str = value.split("-")
        return date(int(year_str), int(month_str), 1)
    except (ValueError, AttributeError):
        raise ValueError(f"planning_month must be 'YYYY-MM', got {value!r}") from None


def _month_minus_one(d: date) -> date:
    """The calendar month immediately before `d` (both as first-of-month
    dates) — docs/14: "the aging reference point is planning month minus
    one calendar month"."""
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


def _resolve_planning_month(session, requested: date | None) -> date:
    """docs/14: required on the first Generate Plan of a cycle, optional
    (reused) on every regeneration after that. Raises ValueError if
    `requested` is None and no New Model 2 plan_run exists yet — callers
    turn that into an HTTP 400 (generate-plan) or an empty/no-op response
    (plan-runs history, finalize — nothing to show/finalize yet either)."""
    if requested is not None:
        return requested
    latest = (
        session.query(PlanRun)
        .filter(PlanRun.model_used == NEW_MODEL_2_PLAN_RUN_LABEL)
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )
    if latest is None:
        raise ValueError("planning_month is required for the first Generate Plan of a cycle")
    return latest.month


def _latest_new_model_2_plan_run(session, planning_month: date | None):
    """New Model 2's own "this cycle's plan_run" (docs/14 fix) — by the
    resolved planning month, never _current_cycle_month() (the ledger-
    ingestion-anchored value every other model still uses unchanged)."""
    if planning_month is None:
        return None
    return (
        session.query(PlanRun)
        .filter(PlanRun.month == planning_month, PlanRun.model_used == NEW_MODEL_2_PLAN_RUN_LABEL)
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )


def _last_new_model_2_allocated_amount(session, vendor_id, planning_month):
    """Mirrors new_model_1.py's _last_new_model_1_allocated_amount exactly,
    scoped to this model's own plan_run family — the frozen Suggested
    figure an overridden vendor had right before they got excluded, not
    recomputed."""
    latest_run = _latest_new_model_2_plan_run(session, planning_month)
    if latest_run is None:
        return 0.0
    allocation = (
        session.query(PlanAllocation)
        .filter(PlanAllocation.plan_run_id == latest_run.id, PlanAllocation.vendor_id == vendor_id)
        .first()
    )
    return float(allocation.allocated_amount) if allocation is not None else 0.0


@router.get("/models/5/plan-runs", response_model=PlanRunHistoryResponse)
def get_new_model_2_plan_runs():
    session = SessionLocal()
    try:
        try:
            planning_month = _resolve_planning_month(session, None)
        except ValueError:
            # No New Model 2 plan_run exists yet this cycle at all — nothing
            # to show, same "empty history" shape every other model's own
            # history endpoint returns before its own first Generate.
            return {"plan_runs": [], "vendor_week_distribution_plans": {}}
        return build_plan_run_history_response(session, 5, cycle_month=planning_month)
    finally:
        session.close()


@router.get("/models/5/minimum-funds-required", response_model=NewModel2MinimumFundsRequiredResponse)
def get_new_model_2_minimum_funds_required(planning_month: str):
    """docs/14 Card 2 — always called with Finance's just-picked planning
    month directly (Card 1), never reused from a stored plan_run: this is
    the one-time, before-any-plan-exists figure, so there's nothing to
    reuse yet on the one call that needs it."""
    try:
        planning_month_date = _parse_planning_month(planning_month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    as_of = _month_minus_one(planning_month_date)
    session = SessionLocal()
    try:
        result = calculate_minimum_funds_required_v2(session=session, as_of=as_of)
        return {
            "total": result["total"],
            "breakdown": result["breakdown"].to_dict(orient="records"),
            "planning_month": planning_month,
            "as_of": as_of,
        }
    finally:
        session.close()


@router.get("/models/5/vendors/{vendor_id}/min-funds-required", response_model=VendorMinFundsRequiredV2Response)
def get_new_model_2_vendor_min_funds_required(vendor_id: int, planning_month: str | None = None):
    """docs/14's vendor detail card "Min Funds Required" section — live,
    real-computed, never hardcoded. planning_month is optional: before
    Finance's first Generate Plan of a cycle, the frontend passes whatever
    it currently has picked in Card 1; afterwards it can omit it and this
    reuses the cycle's stored planning month, same reuse rule as generate-
    plan-and-weekly-view."""
    session = SessionLocal()
    try:
        try:
            requested = _parse_planning_month(planning_month) if planning_month else None
            planning_month_date = _resolve_planning_month(session, requested)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

        as_of = _month_minus_one(planning_month_date)
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        ledger_rows = (
            session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).order_by(MonthlyLedger.month).all()
        )
        detail = min_funds_breakdown_v2(vendor, ledger_rows, as_of=as_of)
        return {**detail, "planning_month": planning_month_date.strftime("%Y-%m"), "as_of": as_of}
    finally:
        session.close()


@router.get("/models/5/vendor-min-funds-required", response_model=AllVendorMinFundsRequiredV2Response)
def get_new_model_2_all_vendor_min_funds_required(planning_month: str | None = None):
    """Fix: New Model 2's own planning table "Total Min Funds" column
    previously always read GET /vendors/{id}/aging's field — the OLD
    carry-forward rule (backend/shared/min_funds.py), same one every other
    tab correctly uses, but New Model 2's own table needs required_amount_v2
    instead. Bulk (one call, every vendor) rather than the vendor-detail
    endpoint's one-at-a-time pattern, since the table renders all of them
    at once.

    Unlike the vendor-detail and Card-2 endpoints, this one does NOT 400
    when no planning month is resolvable yet (no plan_run this cycle, none
    passed) — the table itself must still render before Finance's first
    Generate Plan, just with this column blank ("—") rather than erroring
    on every render.
    """
    session = SessionLocal()
    try:
        try:
            requested = _parse_planning_month(planning_month) if planning_month else None
            planning_month_date = _resolve_planning_month(session, requested)
        except ValueError:
            return {"planning_month": None, "as_of": None, "breakdown": []}

        as_of = _month_minus_one(planning_month_date)
        result = calculate_minimum_funds_required_v2(session=session, as_of=as_of)
        return {
            "planning_month": planning_month_date.strftime("%Y-%m"),
            "as_of": as_of,
            "breakdown": result["breakdown"].to_dict(orient="records"),
        }
    finally:
        session.close()


@router.post("/models/5/generate-plan-and-weekly-view", response_model=NewModel2PlanAndWeeklyViewResponse)
def post_new_model_2_generate_plan_and_weekly_view(body: NewModel2GeneratePlanRequest):
    """New Model 2 has no separate regeneration endpoint (docs/14, same
    reason as New Model 1: the allocation function itself never branches on
    first-vs-regeneration), so every "Generate"/"Regenerate" click for this
    tab calls this same endpoint fresh.
    """
    session = SessionLocal()
    try:
        try:
            requested = _parse_planning_month(body.planning_month) if body.planning_month else None
            planning_month = _resolve_planning_month(session, requested)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        as_of = _month_minus_one(planning_month)

        all_vendors = session.query(Vendor).order_by(Vendor.id).all()
        overridden = [v for v in all_vendors if v.override_amount is not None]
        overridden_ids = {v.id for v in overridden}
        eligible_ids = {v.id for v in all_vendors} - overridden_ids

        total_overridden = sum(float(v.override_amount) for v in overridden)
        effective_funds = max(body.available_funds - total_overridden, 0.0)

        plan = generate_plan(effective_funds, session=session, as_of=as_of, eligible_vendor_ids=eligible_ids)

        if overridden:
            ledger_by_vendor_id = {}
            for row in session.query(MonthlyLedger).filter(MonthlyLedger.vendor_id.in_(overridden_ids)).order_by(
                MonthlyLedger.vendor_id, MonthlyLedger.month
            ):
                ledger_by_vendor_id.setdefault(row.vendor_id, []).append(row)

            frozen_rows = []
            for vendor in overridden:
                ledger_rows = ledger_by_vendor_id.get(vendor.id, [])
                amount, rule = required_amount_v2(vendor, ledger_rows, as_of=as_of)
                frozen_rows.append(
                    {
                        "vendor_id": vendor.id,
                        "erp_code": vendor.erp_code,
                        "vendor_name": vendor.vendor_name,
                        "category": vendor.category.value,
                        "outstanding_balance": live_outstanding_balance(vendor),
                        "required_amount": amount,
                        "rule": rule,
                        "priority_tag": vendor.priority_tag,  # already a plain string (or None)
                        # Frozen — the model never touched this vendor this
                        # round, so their Suggested figure stays exactly
                        # what it was before the override, not recomputed.
                        "allocated_amount": _last_new_model_2_allocated_amount(session, vendor.id, planning_month),
                        "status": AllocationStatus.OVERRIDE_LOCKED.value,
                    }
                )
            plan["allocations"] = pd.concat([plan["allocations"], pd.DataFrame(frozen_rows)], ignore_index=True)

        weekly_view = build_weekly_view(
            plan["allocations"],
            session=session,
            as_of=as_of,
            model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
            funds_figure=body.available_funds,
            # Bug fix (this task): without this, build_weekly_view() always
            # pulled every vendor forward to TODAY's real week, even when
            # Finance is planning a future month — meaningless across two
            # different months' W1-W5. See planner.py's own docstring.
            planning_month=planning_month,
        )
        if weekly_view["plan_run_id"] is not None:
            # build_weekly_view() (shared infra, untouched) stamps `as_of`
            # onto the plan_run it persists — New Model 2's own PlanRun.month
            # must be the actual planning month Finance picked (docs/14),
            # not the aging reference point one calendar month behind it.
            # Fixed up here, post-hoc, rather than by changing planner.py's
            # own shared behavior for every other model.
            session.query(PlanRun).filter_by(id=weekly_view["plan_run_id"]).update({"month": planning_month})
        session.commit()
        return {
            "plan": _plan_response(plan),
            "weekly_view": {
                "weekly_summary": weekly_view["weekly_summary"].reset_index().to_dict(orient="records"),
                "detail": weekly_view["detail"].to_dict(orient="records"),
                "plan_run_id": weekly_view["plan_run_id"],
            },
            "total_overridden": total_overridden,
            "funds_left_for_regeneration": effective_funds,
        }
    finally:
        session.close()


@router.post("/models/5/finalize", response_model=FinalizeCheckResponse)
def post_new_model_2_finalize():
    """docs/14's three-option shortfall check, mirrors New Model 1's own
    POST /models/4/finalize exactly (see that router's docstring — not
    re-explained here): reconciles the CURRENT state (any overrides
    applied) of New Model 2's latest plan_run against the funds figure it
    was generated with, and publishes the Budget snapshot
    (finalize_plan(model_number=5)) only when the check comes back clean.
    """
    session = SessionLocal()
    try:
        try:
            planning_month = _resolve_planning_month(session, None)
        except ValueError:
            planning_month = None
        latest_run = _latest_new_model_2_plan_run(session, planning_month)
        if latest_run is None or latest_run.funds_figure is None:
            vendor_count = finalize_plan(session, model_number=5)
            session.commit()
            return {
                "ok": True,
                "total_committed": 0.0,
                "available_funds": 0.0,
                "over_by": 0.0,
                "responsible_vendors": [],
                "vendor_count": vendor_count,
            }

        allocations = session.query(PlanAllocation).filter_by(plan_run_id=latest_run.id).all()
        available_funds = float(latest_run.funds_figure)

        total_committed = 0.0
        responsible: list[dict[str, Any]] = []
        for allocation in allocations:
            suggested = float(allocation.allocated_amount)
            override = float(allocation.override_amount) if allocation.override_amount is not None else None
            total_committed += override if override is not None else suggested
            if override is not None and override > suggested + MONEY_EPSILON:
                vendor = session.query(Vendor).filter_by(id=allocation.vendor_id).one()
                responsible.append(
                    {
                        "vendor_id": vendor.id,
                        "erp_code": vendor.erp_code,
                        "vendor_name": vendor.vendor_name,
                        "suggested_amount": suggested,
                        "override_amount": override,
                    }
                )

        over_by = max(total_committed - available_funds, 0.0)
        ok = over_by <= MONEY_EPSILON
        vendor_count = finalize_plan(session, model_number=5) if ok else 0
        session.commit()
        return {
            "ok": ok,
            "total_committed": total_committed,
            "available_funds": available_funds,
            "over_by": over_by,
            "responsible_vendors": responsible,
            "vendor_count": vendor_count,
        }
    finally:
        session.close()
