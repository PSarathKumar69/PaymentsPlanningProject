"""Model 1 router — weighted-score ranking + plan generation.

Model 1 is now the sole model that drives payment_status/cycle_allocation
(backend/shared/payment_logging.py) — its plan_run's model_used must be
exactly MODEL_1_PLAN_RUN_LABEL, the same constant that lookup filters on.
"""
from typing import Any

import pandas as pd
from fastapi import APIRouter

from backend.db.models import MonthlyLedger, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import generate_plan, score_vendors
from backend.shared.constants import MODEL_1_PLAN_RUN_LABEL
from backend.shared.enums import AllocationStatus
from backend.shared.min_funds import live_outstanding_balance, required_amount
from backend.shared.payment_logging import _current_cycle_month
from backend.shared.plan_history import build_plan_run_history_response
from backend.weekly_planning.planner import build_weekly_view

from ..schemas.models import GeneratePlanRequest, GeneratePlanResponse
from ..schemas.plan_runs import PlanRunHistoryResponse
from ..schemas.weekly_planning import GeneratePlanAndWeeklyViewResponse

router = APIRouter(tags=["model1"])


def _plan_response(result: dict) -> dict:
    return {**result, "allocations": result["allocations"].to_dict(orient="records")}


def _last_model1_allocated_amount(session, vendor_id):
    """The model's own last-computed allocated_amount for this vendor, before
    they became override-excluded — frozen, not recomputed (confirmed:
    Finance can still see "model suggested X, I overrode to Y" as two
    distinct numbers). Same "latest MODEL_1_PLAN_RUN_LABEL plan_run this
    cycle month" lookup pattern as payment_logging.py's
    _this_cycle_allocation(), deliberately not reused directly since that
    function returns the override itself once one is set — here we want the
    figure it was overriding.
    """
    cycle_month = _current_cycle_month(session)
    latest_run = (
        session.query(PlanRun)
        .filter(PlanRun.month == cycle_month, PlanRun.model_used == MODEL_1_PLAN_RUN_LABEL)
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )
    if latest_run is None:
        return 0.0
    allocation = (
        session.query(PlanAllocation)
        .filter(PlanAllocation.plan_run_id == latest_run.id, PlanAllocation.vendor_id == vendor_id)
        .first()
    )
    return float(allocation.allocated_amount) if allocation is not None else 0.0


@router.get("/models/1/scores", response_model=list[dict[str, Any]])
def get_model1_scores():
    return score_vendors(session=None, as_of=None).to_dict(orient="records")


@router.get("/models/1/plan-runs", response_model=PlanRunHistoryResponse)
def get_model1_plan_runs():
    """Every PlanRun for Model 1 (model_used == MODEL_1_PLAN_RUN_LABEL, no
    regeneration variant to worry about) + the current cycle month, oldest
    first, each with its full allocations. See
    backend/shared/plan_history.py::build_plan_run_history_response for the
    response shape, and where the frontend should read a vendor's current
    week_distribution_plan from (vendor_week_distribution_plans, not the
    per-allocation rows)."""
    session = SessionLocal()
    try:
        return build_plan_run_history_response(session, 1)
    finally:
        session.close()


@router.post("/models/1/generate-plan", response_model=GeneratePlanResponse)
def post_model1_generate_plan(body: GeneratePlanRequest):
    result = generate_plan(body.available_funds, session=None, as_of=None, eligible_vendor_ids=None)
    return _plan_response(result)


@router.post("/models/1/generate-plan-and-weekly-view", response_model=GeneratePlanAndWeeklyViewResponse)
def post_model1_generate_plan_and_weekly_view(body: GeneratePlanRequest):
    """Model 1 has no separate regeneration endpoint (docs/03) — every
    "Regenerate" click for Model 1 calls this same endpoint fresh. Confirmed
    new behavior: a vendor with a currently-set vendor.override_amount is
    excluded from Model 1's computation entirely (their allocation is
    already decided) and their override amount is removed from the funds
    pool fed to everyone else, so it can't be redistributed a second time.
    """
    session = SessionLocal()
    try:
        all_vendors = session.query(Vendor).order_by(Vendor.id).all()
        overridden = [v for v in all_vendors if v.override_amount is not None]
        overridden_ids = {v.id for v in overridden}
        eligible_ids = {v.id for v in all_vendors} - overridden_ids

        total_overridden = sum(float(v.override_amount) for v in overridden)
        effective_funds = max(body.available_funds - total_overridden, 0.0)

        plan = generate_plan(effective_funds, session=session, as_of=None, eligible_vendor_ids=eligible_ids)

        if overridden:
            ledger_by_vendor_id = {}
            for row in session.query(MonthlyLedger).filter(MonthlyLedger.vendor_id.in_(overridden_ids)).order_by(
                MonthlyLedger.vendor_id, MonthlyLedger.month
            ):
                ledger_by_vendor_id.setdefault(row.vendor_id, []).append(row)
            scores = score_vendors(session=session, as_of=None).set_index("vendor_id")["score"]

            frozen_rows = []
            for vendor in overridden:
                ledger_rows = ledger_by_vendor_id.get(vendor.id, [])
                amount, rule = required_amount(vendor, ledger_rows, as_of=None)
                frozen_rows.append(
                    {
                        "vendor_id": vendor.id,
                        "erp_code": vendor.erp_code,
                        "vendor_name": vendor.vendor_name,
                        "category": vendor.category.value,
                        "outstanding_balance": live_outstanding_balance(vendor),
                        "required_amount": amount,
                        "rule": rule,
                        "score": float(scores.get(vendor.id, 0.0)),
                        # Frozen — the model never touched this vendor this
                        # round, so their Suggested figure stays exactly what
                        # it was before the override, not recomputed and not
                        # a mirror of the override amount.
                        "allocated_amount": _last_model1_allocated_amount(session, vendor.id),
                        "status": AllocationStatus.OVERRIDE_LOCKED.value,
                    }
                )
            plan["allocations"] = pd.concat([plan["allocations"], pd.DataFrame(frozen_rows)], ignore_index=True)

        # funds_figure stays the raw request figure (not effective_funds) —
        # plan_allocations.py's funds_warning check compares every sibling
        # row's committed amount (override, else allocated_amount) against
        # this, and that total already includes the overridden amounts
        # directly, so the comparison must be against the real total funds
        # available this cycle, not the pool reduced by those same overrides.
        weekly_view = build_weekly_view(
            plan["allocations"], session=session, as_of=None, model_used=MODEL_1_PLAN_RUN_LABEL, funds_figure=body.available_funds
        )
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
