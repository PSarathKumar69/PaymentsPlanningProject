"""Regeneration router. Skips the rule-4 minimum-funds gate entirely
(confirmed in docs/06) — no gating logic here, initial or otherwise."""
from fastapi import APIRouter

from backend.models.model1_weighted_scoring.scorer import generate_plan as model1_generate_plan
from backend.models.model2_min_funds_advisory.advisor import generate_plan as model2_generate_plan
from backend.models.model3_priority_bucket.bucketer import generate_plan as model3_generate_plan
from backend.shared.constants import MODEL_1_PLAN_RUN_LABEL
from backend.weekly_planning.regeneration import regenerate

from ..schemas.weekly_planning import RegenerateRequest, RegenerateResponse

router = APIRouter(tags=["regeneration"])

_MODEL_FNS = {1: model1_generate_plan, 2: model2_generate_plan, 3: model3_generate_plan}


@router.post("/regenerate", response_model=RegenerateResponse)
def post_regenerate(body: RegenerateRequest):
    if body.model not in _MODEL_FNS:
        raise ValueError(f"model must be 1, 2, or 3, got {body.model!r}")

    # Model 1 drives payment_status/cycle_allocation (payment_logging.py's
    # _this_cycle_allocation() filters on MODEL_1_PLAN_RUN_LABEL exactly) —
    # a regenerated Model 1 plan must be tagged the same as a freshly
    # generated one, not a derived "model1_regeneration" label, or it would
    # be invisible to that lookup and payment status would keep reading a
    # stale earlier plan_run. Models 2/3 don't drive payment status, so
    # their own "model{n}_regeneration" labeling is unaffected.
    model_used = MODEL_1_PLAN_RUN_LABEL if body.model == 1 else f"model{body.model}_regeneration"

    result = regenerate(
        _MODEL_FNS[body.model],
        body.available_funds,
        body.current_week,
        reconsider_decisions=body.reconsider_decisions,
        session=None,
        model_used=model_used,
    )
    return {
        "plan_run_id": result["plan_run_id"],
        "allocations": result["allocations"].to_dict(orient="records"),
        "excluded": result["excluded"],
        "pulled_forward": result["pulled_forward"],
        "unallocated_surplus": result["unallocated_surplus"],
    }
