"""Model 2 router — minimum-funds advisory + plan generation.

Rule 4 sequencing (CLAUDE.md): /models/2/minimum-funds-required must be
shown to Finance before the funds-input field is usable for ANY model's
Generate Plan. That ordering is enforced by the HTML test UI, not here —
this API is stateless: each endpoint just computes from its inputs, with
no memory of whether another endpoint was called first.
# DEFAULT — confirm with Sarath: stateless API, UI enforces order.
"""
from fastapi import APIRouter

from backend.db.session import SessionLocal
from backend.models.model2_min_funds_advisory.advisor import calculate_minimum_funds_required, generate_plan
from backend.shared.plan_history import build_plan_run_history_response
from backend.weekly_planning.planner import build_weekly_view

from ..schemas.models import GeneratePlanRequest, GeneratePlanResponse, MinimumFundsRequiredResponse
from ..schemas.plan_runs import PlanRunHistoryResponse
from ..schemas.weekly_planning import GeneratePlanAndWeeklyViewResponse

router = APIRouter(tags=["model2"])


def _plan_response(result: dict) -> dict:
    return {**result, "allocations": result["allocations"].to_dict(orient="records")}


@router.get("/models/2/minimum-funds-required", response_model=MinimumFundsRequiredResponse)
def get_model2_minimum_funds_required():
    result = calculate_minimum_funds_required(session=None, as_of=None)
    return {**result, "breakdown": result["breakdown"].to_dict(orient="records")}


@router.get("/models/2/plan-runs", response_model=PlanRunHistoryResponse)
def get_model2_plan_runs():
    """Every PlanRun for Model 2's family ("model2" + "model2_regeneration")
    + the current cycle month, oldest first, each with its full
    allocations. See backend/shared/plan_history.py's
    build_plan_run_history_response for the response shape, and where the
    frontend should read a vendor's current week_distribution_plan from
    (vendor_week_distribution_plans, not the per-allocation rows)."""
    session = SessionLocal()
    try:
        return build_plan_run_history_response(session, 2)
    finally:
        session.close()


@router.post("/models/2/generate-plan", response_model=GeneratePlanResponse)
def post_model2_generate_plan(body: GeneratePlanRequest):
    result = generate_plan(body.available_funds, session=None, as_of=None, eligible_vendor_ids=None)
    return _plan_response(result)


@router.post("/models/2/generate-plan-and-weekly-view", response_model=GeneratePlanAndWeeklyViewResponse)
def post_model2_generate_plan_and_weekly_view(body: GeneratePlanRequest):
    plan = generate_plan(body.available_funds, session=None, as_of=None, eligible_vendor_ids=None)
    weekly_view = build_weekly_view(
        plan["allocations"], session=None, as_of=None, model_used="model2", funds_figure=body.available_funds
    )
    return {
        "plan": _plan_response(plan),
        "weekly_view": {
            "weekly_summary": weekly_view["weekly_summary"].reset_index().to_dict(orient="records"),
            "detail": weekly_view["detail"].to_dict(orient="records"),
            "plan_run_id": weekly_view["plan_run_id"],
        },
    }
