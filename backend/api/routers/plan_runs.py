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
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.db.models import AuditLog, PlanAllocation, PlanRun
from backend.db.session import SessionLocal
from backend.shared.enums import ChangeSource

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
        return {"plan_run_id": plan_run_id, "deleted_allocations": deleted_allocations}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
