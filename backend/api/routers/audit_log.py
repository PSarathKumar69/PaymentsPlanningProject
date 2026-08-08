"""Audit log router — read-only, append-only log listing.

Finance-friendly rework (Configuration module UI/UX task, docs/11): left-
joins vendors so callers get vendor_name/erp_code directly instead of a
bare vendor_id, adds search/source/date filters, and paginates
(limit/offset) instead of returning the whole table unbounded.
"""
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import or_

from backend.db.models import AuditLog, Vendor
from backend.db.session import SessionLocal

router = APIRouter(tags=["audit_log"])

MAX_LIMIT = 500
DEFAULT_LIMIT = 100


@router.get("/audit-log", response_model=dict[str, Any])
def list_audit_log(
    vendor_id: int | None = None,
    search: str | None = None,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
):
    session = SessionLocal()
    try:
        query = session.query(AuditLog, Vendor.vendor_name, Vendor.erp_code).outerjoin(
            Vendor, AuditLog.vendor_id == Vendor.id
        )
        if vendor_id is not None:
            query = query.filter(AuditLog.vendor_id == vendor_id)
        if search:
            like = f"%{search}%"
            query = query.filter(or_(Vendor.vendor_name.ilike(like), Vendor.erp_code.ilike(like)))
        if source is not None:
            query = query.filter(AuditLog.source == source)
        if date_from is not None:
            query = query.filter(AuditLog.timestamp >= datetime.combine(date_from, datetime.min.time()))
        if date_to is not None:
            query = query.filter(AuditLog.timestamp < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

        total = query.count()
        limit = max(1, min(limit, MAX_LIMIT))
        rows = query.order_by(AuditLog.id.desc()).offset(max(0, offset)).limit(limit).all()
        return {
            "total": total,
            "items": [
                {
                    "id": r.id,
                    "timestamp": r.timestamp,
                    "vendor_id": r.vendor_id,
                    "vendor_name": vendor_name,
                    "erp_code": erp_code,
                    "field_name": r.field_name,
                    "old_value": r.old_value,
                    "new_value": r.new_value,
                    "source": r.source,
                    "changed_by": r.changed_by,
                }
                for r, vendor_name, erp_code in rows
            ],
        }
    finally:
        session.close()
