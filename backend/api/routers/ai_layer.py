"""AI layer router — zero-allocation vendor talking scripts (Gemini), plus
New Model 2's any-status vendor talking points companion.

Wraps Gemini/network failures into a clean error, not an unhandled 500. A
missing/placeholder API key raises ValueError from gemini_client, which the
app-level exception_handler already turns into a clean 400 — re-raised here
unchanged so it isn't caught by the generic Exception branch below.
_vendor_companion_fields() below can also raise ValueError before any Gemini
call is even made (e.g. no New Model 2 plan generated yet this cycle) — same
400 treatment, same reason.

The prior "broader Q&A" mode (POST /ai/ask, plan-summary fact pack) was
removed in this task per Sarath's own scope correction ("this functionality
is enough" — vendor talking points only). Nothing here reintroduces it.
"""
from fastapi import APIRouter, HTTPException

from backend.ai_layer import companion
from backend.ai_layer.talking_script import generate_talking_scripts
from backend.db.models import MonthlyLedger, PlanAllocation, Vendor
from backend.db.session import SessionLocal
from backend.shared.aging import compute_vendor_aging
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import AllocationStatus, VendorCategory
from backend.shared.min_funds_v2 import min_funds_breakdown_v2, required_amount_v2

from .new_model_2 import _latest_new_model_2_plan_run, _month_minus_one, _resolve_planning_month
from ..schemas.ai_layer import TalkingScriptsRequest, TalkingScriptsResponse, VendorTalkingPointsRequest, VendorTalkingPointsResponse

router = APIRouter(tags=["ai_layer"])


@router.post("/ai/talking-scripts", response_model=TalkingScriptsResponse)
def post_talking_scripts(body: TalkingScriptsRequest):
    try:
        scripts = generate_talking_scripts(body.vendor_allocations)
    except ValueError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini call failed: {exc}") from exc
    return {"scripts": scripts}


def _vendor_status_and_required(vendor, ledger_rows, allocation, as_of):
    """Same FULL/ZERO/PARTIAL derivation backend/models/new_model_2/allocator.py
    itself uses (allocated vs required_amount_v2), for a vendor whose plan row
    isn't otherwise available (no status column persisted on PlanAllocation —
    only allocated_amount is)."""
    required_amount, rule = required_amount_v2(vendor, ledger_rows, as_of=as_of)
    allocated_amount = float(allocation.allocated_amount) if allocation is not None else 0.0
    if vendor.category in (VendorCategory.MUST_PAY, VendorCategory.COMMITMENT):
        status = AllocationStatus.GUARANTEED.value
    elif vendor.override_amount is not None:
        status = AllocationStatus.OVERRIDE_LOCKED.value
    elif allocated_amount >= required_amount - MONEY_EPSILON:
        status = AllocationStatus.FULL.value
    elif allocated_amount <= MONEY_EPSILON:
        status = AllocationStatus.ZERO.value
    else:
        status = AllocationStatus.PARTIAL.value
    return required_amount, rule, allocated_amount, status


def _vendor_companion_fields(session, vendor_id):
    """Gathers this vendor's already-computed facts (aging, min funds
    breakdown, this cycle's allocation) from the exact same functions the
    vendor detail card itself calls — nothing new computed here, just
    assembled for context_builder.build_vendor_talking_points_fact_pack()."""
    planning_month_date = _resolve_planning_month(session, None)
    as_of = _month_minus_one(planning_month_date)

    vendor = session.query(Vendor).filter_by(id=vendor_id).one()
    ledger_rows = session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).order_by(MonthlyLedger.month).all()

    aging = compute_vendor_aging(
        ledger_rows, as_of=as_of, already_paid_this_cycle=float(vendor.paid_so_far_this_month)
    )
    min_funds_breakdown = min_funds_breakdown_v2(vendor, ledger_rows, as_of=as_of)

    latest_run = _latest_new_model_2_plan_run(session, planning_month_date)
    allocation = None
    if latest_run is not None:
        allocation = (
            session.query(PlanAllocation)
            .filter_by(plan_run_id=latest_run.id, vendor_id=vendor_id)
            .first()
        )
    required_amount, _rule, allocated_amount, status = _vendor_status_and_required(
        vendor, ledger_rows, allocation, as_of
    )

    return {
        "vendor_id": vendor.id,
        "erp_code": vendor.erp_code,
        "vendor_name": vendor.vendor_name,
        "category": vendor.category.value,
        "priority_tag": vendor.priority_tag,  # already a plain string (or None)
        "aging": {
            "oldest_bucket": aging.oldest_bucket,
            "oldest_bucket_months_back": aging.oldest_bucket_months_back,
            "bucket_balances": aging.bucket_balances,
            "total_outstanding": aging.total_outstanding,
            "oldest_bucket_amount": aging.oldest_bucket_amount,
            "monthly_breakdown": aging.monthly_breakdown,
        },
        "min_funds_breakdown": min_funds_breakdown,
        "allocated_amount": allocated_amount,
        "required_amount": required_amount,
        "status": status,
    }


@router.post("/ai/vendor-talking-points", response_model=VendorTalkingPointsResponse)
def post_vendor_talking_points(body: VendorTalkingPointsRequest):
    """Any-status generalization of POST /ai/talking-scripts above (docs: "it
    is now calling and talking to vendors") — full/partial/zero, not just
    zero. Kept as its own endpoint so the existing zero-only script/button
    stays completely unaffected."""
    session = SessionLocal()
    try:
        vendor_fields = _vendor_companion_fields(session, body.vendor_id)
        script_text = companion.generate_vendor_talking_points(**vendor_fields)
        return {
            "vendor_id": vendor_fields["vendor_id"],
            "erp_code": vendor_fields["erp_code"],
            "vendor_name": vendor_fields["vendor_name"],
            "script_text": script_text,
        }
    except ValueError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini call failed: {exc}") from exc
    finally:
        session.close()
