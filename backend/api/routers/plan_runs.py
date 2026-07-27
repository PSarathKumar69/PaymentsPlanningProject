"""Plan-run hard delete.

DELETE is a real, hard delete of the plan_run + a cascade of its
plan_allocations — an explicit decision (not a soft-delete/deleted_at
column): PlanRun.allocations has no cascade="all, delete-orphan" on its
relationship (backend/db/models.py), so PlanAllocation rows are deleted
explicitly first, then the plan_run, in one transaction.

If the deleted plan_run happened to be the current latest one for its model
family, every "latest plan_run" lookup elsewhere (payment_logging.py's
_this_cycle_allocation(), model1.py's _last_model1_allocated_amount(),
plan_history.py's is_latest_plan_run()) naturally falls back to treating the
next-most-recent remaining plan_run as latest once this row is gone — same
order_by(created_at desc, id desc).first() pattern used everywhere already.
No special-casing needed here.

Bug fix (this task): deleting a plan_run used to leave any vendor overridden
IN it still locked (Vendor.override_amount is sticky/vendor-level, untouched
by deleting the plan_run that reflected it) — so the next generate still
excluded them, but the "frozen prior Suggested figure" lookup now found no
plan_run at all to read from and fell back to 0.0, showing a locked vendor
with nothing allocated. Fix: when the deleted plan_run was the LATEST for
its model family (i.e. nothing newer still legitimately depends on that
override), clear Vendor.override_amount for every vendor whose row in THIS
plan_run actually had an override_amount set. An older plan_run being
deleted (not the latest) never touches overrides — a newer, undeleted
plan_run may still be relying on that same override.
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.db.models import AuditLog, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.shared.enums import ChangeSource
from backend.shared.plan_history import is_latest_plan_run

from ..schemas.plan_runs import DeletePlanRunResponse

router = APIRouter(tags=["plan-runs"])


@router.delete("/plan-runs/{plan_run_id}", response_model=DeletePlanRunResponse)
def delete_plan_run(plan_run_id: int):
    session = SessionLocal()
    try:
        plan_run = session.query(PlanRun).filter_by(id=plan_run_id).first()
        if plan_run is None:
            raise HTTPException(status_code=404, detail=f"plan_run {plan_run_id} not found")

        # Read before the delete below expires/removes these attributes.
        summary = f"model_used={plan_run.model_used}, created_at={plan_run.created_at.isoformat()}"
        was_latest = is_latest_plan_run(session, plan_run)

        allocations = session.query(PlanAllocation).filter_by(plan_run_id=plan_run_id).all()
        cleared_override_vendor_ids = []
        if was_latest:
            for allocation in allocations:
                if allocation.override_amount is None:
                    continue
                vendor = session.query(Vendor).filter_by(id=allocation.vendor_id).one()
                if vendor.override_amount is None:
                    continue
                old_value = float(vendor.override_amount)
                vendor.override_amount = None
                cleared_override_vendor_ids.append(vendor.id)
                session.add(
                    AuditLog(
                        timestamp=datetime.now(),
                        vendor_id=vendor.id,
                        field_name="vendor.override_amount",
                        old_value=str(old_value),
                        new_value=None,
                        source=ChangeSource.UI_EDIT.value,
                    )
                )

        deleted_allocations = (
            session.query(PlanAllocation)
            .filter_by(plan_run_id=plan_run_id)
            .delete(synchronize_session=False)
        )
        session.delete(plan_run)

        # AuditLog.vendor_id is nullable (backend/db/models.py) — a single
        # row with vendor_id=None fits the existing schema without needing
        # one row per affected vendor.
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=None,
                field_name="plan_run_deleted",
                old_value=f"plan_run_id={plan_run_id} ({summary})",
                new_value="deleted",
                source=ChangeSource.UI_EDIT.value,
            )
        )
        session.commit()
        return {
            "plan_run_id": plan_run_id,
            "deleted_allocations": deleted_allocations,
            "cleared_override_vendor_ids": cleared_override_vendor_ids,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
