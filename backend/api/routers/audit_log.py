"""Audit log router — read-only, append-only log listing."""
from typing import Any

from fastapi import APIRouter

from backend.db.models import AuditLog
from backend.db.session import SessionLocal

router = APIRouter(tags=["audit_log"])


@router.get("/audit-log", response_model=list[dict[str, Any]])
def list_audit_log(vendor_id: int | None = None):
    session = SessionLocal()
    try:
        query = session.query(AuditLog)
        if vendor_id is not None:
            query = query.filter_by(vendor_id=vendor_id)
        rows = query.order_by(AuditLog.id.desc()).all()
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp,
                "vendor_id": r.vendor_id,
                "field_name": r.field_name,
                "old_value": r.old_value,
                "new_value": r.new_value,
                "source": r.source,
                "changed_by": r.changed_by,
            }
            for r in rows
        ]
    finally:
        session.close()
