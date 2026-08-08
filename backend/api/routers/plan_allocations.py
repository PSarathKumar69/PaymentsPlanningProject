"""Plan-allocation overrides — Finance's manual override on top of a
model's suggested allocated_amount. New Model 2 (the sole remaining model)
plus this override now drive payment_status/cycle_allocation
(backend/shared/payment_logging.py) — the column/endpoint stayed
model-agnostic at the DB/API level throughout.
"""
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.api.routers.new_model_2 import _month_minus_one
from backend.db.models import AuditLog, PlanAllocation, Vendor
from backend.db.session import SessionLocal
from backend.models.new_model_2.allocator import generate_plan
from backend.shared.constants import MONEY_EPSILON, NEW_MODEL_2_PLAN_RUN_LABEL
from backend.shared.enums import ChangeSource
from backend.shared.plan_history import is_latest_plan_run, model_family_prefix

from ..schemas.plan_allocations import (
    OverrideRequest,
    OverrideResponse,
    WeekDistributionRequest,
    WeekDistributionResponse,
)

router = APIRouter(tags=["plan-allocations"])


@router.patch("/plan-allocations/{plan_allocation_id}/override", response_model=OverrideResponse)
def patch_plan_allocation_override(plan_allocation_id: int, body: OverrideRequest):
    """Vendor.override_amount is the sticky, per-vendor, whole-month source
    of truth (docs/06 fix) — this endpoint writes it there AND stamps the
    same value onto the current PlanAllocation row it's called against, so
    the two never drift apart. Never dropped/capped/silently changed by the
    system; the only non-blocking exception is the funds-pool warning below,
    which flags but never rejects (CLAUDE.md rule 2 — suggest, never enforce).
    """
    session = SessionLocal()
    try:
        allocation = session.query(PlanAllocation).filter_by(id=plan_allocation_id).one()
        vendor = session.query(Vendor).filter_by(id=allocation.vendor_id).one()

        # Only the CURRENT latest plan_run (within this allocation's own
        # model family -- "model2" and "model2_regeneration" count as the
        # same family, see backend/shared/plan_history.py) is editable -- an
        # override tied to a past plan_run is rejected with 409, so Finance
        # can't silently rewrite a superseded plan.
        #
        # New Model 2 (docs/14) is the one family whose "cycle" is Finance's
        # own picked planning month, not the ledger-ingestion-anchored
        # _current_cycle_month() is_latest_plan_run() uses by default for
        # every other model — passing plan_run.month itself here (every row
        # in one New Model 2 cycle shares that same value, by construction)
        # gets the right answer without this generic, model-agnostic
        # endpoint needing to special-case New Model 2's own planning-month
        # resolution logic.
        cycle_month_override = (
            allocation.plan_run.month
            if model_family_prefix(allocation.plan_run.model_used) == NEW_MODEL_2_PLAN_RUN_LABEL
            else None
        )
        if not is_latest_plan_run(session, allocation.plan_run, cycle_month=cycle_month_override):
            raise HTTPException(
                status_code=409,
                detail="Cannot edit override on a past plan - only the latest plan is editable",
            )

        new_value = body.override_amount
        # Same ledger-integrity ceiling used elsewhere: never above what's
        # actually outstanding. Reject, don't silently clamp — Finance
        # should see why an entry didn't take.
        if new_value is not None and not (0 <= new_value <= float(vendor.opening_balance)):
            raise ValueError(
                f"{vendor.erp_code}: override_amount {new_value:.2f} must be between 0 and "
                f"the outstanding balance {float(vendor.opening_balance):.2f}"
            )

        old_value = float(vendor.override_amount) if vendor.override_amount is not None else None
        vendor.override_amount = new_value
        allocation.override_amount = new_value
        allocated_amount = float(allocation.allocated_amount)  # read before commit expires the attribute

        # Bug fix (this task, confirmed real repro on vendor V00080/Bluevine):
        # the Distribution week bucket must always follow the current
        # override — an EARLIER version of this fix only rescaled while
        # Vendor.week_distribution_plan was still None (never manually
        # touched), on the theory that a sticky manual split is Finance's
        # own and must never be silently rewritten. In practice Finance
        # expects an override to always update the weekly figure, sticky
        # split or not — Bluevine had a one-week sticky split left over from
        # an earlier manual edit, and every subsequent override on that
        # vendor was silently ignored by the Distribution column forever
        # after. Now always rescales whatever distribution currently exists
        # (sticky or the plain single-week default) proportionally to the
        # new effective total, so a genuine multi-week manual split keeps
        # its relative weights while the common single-week case just gets
        # its one number updated — and writes through to BOTH
        # Vendor.week_distribution_plan (the sticky field) and this
        # allocation's own snapshot, so the sticky value itself never goes
        # stale again on a later regeneration (planner.py stamps it as-is).
        effective_amount = new_value if new_value is not None else allocated_amount
        current_distribution = vendor.week_distribution_plan or allocation.week_distribution_plan
        if current_distribution:
            old_total = sum(current_distribution.values())
            if old_total > MONEY_EPSILON:
                new_distribution = {
                    week: amount / old_total * effective_amount for week, amount in current_distribution.items()
                }
            elif len(current_distribution) == 1:
                new_distribution = {next(iter(current_distribution)): effective_amount}
            else:
                new_distribution = {str(allocation.assigned_week): effective_amount}
            vendor.week_distribution_plan = new_distribution
            allocation.week_distribution_plan = new_distribution

        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=vendor.id,
                field_name="vendor.override_amount",
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                source=ChangeSource.UI_EDIT.value,
            )
        )

        # Non-blocking funds-pool check (docs/06 fix): every vendor's
        # effective committed amount (override, else the model's own
        # allocated_amount) across this same plan_run, compared against the
        # round's funds figure. Recomputed on every save, not just when the
        # override is first set, since it must reflect whatever the current
        # funds figure is right now.
        #
        # total_committed is a plain sum of this plan_run's own
        # PlanAllocation rows (informational only). over_by is NOT derived
        # from that sum — it's the same cuttable-shortfall figure the Funds
        # Left card shows (-leftover_remaining, floored at 0), recomputed
        # live via the same generate_plan() call the Generate/Regenerate
        # endpoint uses (overridden vendors excluded, their amount
        # pre-subtracted from available_funds). Two real bugs the
        # PlanAllocation-sum approach had (same fix applied to
        # new_model_2.py's Finalize check, for the same reasons): (1) a
        # flat total_committed-vs-available_funds sum double-counts the
        # guaranteed (Must Pay/Commitment) tier's own inherent shortfall as
        # if it were a cuttable Normal/Inactive one; (2) PlanAllocation rows
        # only exist for vendors planner.py's build_weekly_view() actually
        # placed into a week (Vendor.assigned_week set) — a vendor with no
        # assigned_week yet is silently absent from this plan_run's
        # allocations entirely, so a PlanAllocation-only sum under-counts
        # real production data with such vendors.
        funds_warning = None
        plan_run = allocation.plan_run
        if plan_run.funds_figure is not None:
            sibling_rows = session.query(PlanAllocation).filter_by(plan_run_id=allocation.plan_run_id).all()
            total_committed = sum(
                float(r.override_amount) if r.override_amount is not None else float(r.allocated_amount)
                for r in sibling_rows
            )
            available_funds = float(plan_run.funds_figure)

            all_vendors = session.query(Vendor).order_by(Vendor.id).all()
            overridden = [v for v in all_vendors if v.override_amount is not None]
            overridden_ids = {v.id for v in overridden}
            eligible_ids = {v.id for v in all_vendors} - overridden_ids
            total_overridden = sum(float(v.override_amount) for v in overridden)
            effective_funds = max(available_funds - total_overridden, 0.0)
            as_of = _month_minus_one(plan_run.month)
            live_plan = generate_plan(effective_funds, session=session, as_of=as_of, eligible_vendor_ids=eligible_ids)
            over_by = max(-live_plan["leftover_remaining"], 0.0)
            if over_by > MONEY_EPSILON:
                funds_warning = {
                    "total_committed": total_committed,
                    "available_funds": available_funds,
                    "over_by": over_by,
                }

        session.commit()

        return {
            "plan_allocation_id": plan_allocation_id,
            "vendor_id": vendor.id,
            "allocated_amount": allocated_amount,
            "override_amount": new_value,
            "effective_amount": new_value if new_value is not None else allocated_amount,
            "funds_warning": funds_warning,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.patch("/plan-allocations/{plan_allocation_id}/week-distribution", response_model=WeekDistributionResponse)
def patch_week_distribution(plan_allocation_id: int, body: WeekDistributionRequest):
    """Finance's own free-form planned-distribution notes — purely
    additive/display-only (docs: never affects payment_status, ledger
    integrity, or override math, on any model). Partial update: only the
    weeks present in the request body are touched; every other week already
    stored is left untouched. Deliberately no validation that the values
    sum to anything, and no ledger-integrity ceiling — this is not a real
    payment decision.

    Distribution is now vendor-level/cycle-level, not per-plan-run (this
    task, mirrors Vendor.override_amount's exact dual-write pattern):
    Vendor.week_distribution_plan is the live, sticky "current" value the
    UI reads going forward, and this specific PlanAllocation row keeps
    getting the same final merged value stamped onto it too, purely as a
    frozen historical/audit snapshot of what the distribution was AT THIS
    EDIT. No latest-plan_run guard here (unlike the override endpoint) --
    per this task's decision, distribution stays editable regardless of
    which plan_run's row triggered the call, since there's no "past plan"
    concept for a value that isn't scoped to one plan_run anymore.
    """
    session = SessionLocal()
    try:
        allocation = session.query(PlanAllocation).filter_by(id=plan_allocation_id).one()
        vendor = session.query(Vendor).filter_by(id=allocation.vendor_id).one()

        # Base off the vendor-level sticky value once one exists; the very
        # first edit ever (vendor.week_distribution_plan still None) falls
        # back to this row's own existing snapshot, so it doesn't lose the
        # freshly-seeded default ({assigned_week: allocated_amount}) that
        # planner.py/regeneration.py already put there.
        old_value = (
            dict(vendor.week_distribution_plan)
            if vendor.week_distribution_plan is not None
            else dict(allocation.week_distribution_plan or {})
        )
        new_value = {**old_value, **body.updates}
        vendor.week_distribution_plan = new_value
        allocation.week_distribution_plan = new_value

        # Audited the same way the override endpoint is, for consistency —
        # flagged in the report: this is non-enforced, display-only data
        # with no real financial consequence, so whether it belongs in the
        # same audit trail as consequential overrides is a judgment call
        # worth confirming rather than assumed either way.
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=vendor.id,
                field_name="plan_allocation.week_distribution_plan",
                old_value=json.dumps(old_value),
                new_value=json.dumps(new_value),
                source=ChangeSource.UI_EDIT.value,
            )
        )
        session.commit()

        return {
            "plan_allocation_id": plan_allocation_id,
            "vendor_id": vendor.id,
            "week_distribution_plan": new_value,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
