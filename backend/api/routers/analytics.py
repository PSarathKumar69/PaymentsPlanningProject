"""Analytics dashboard router — thin request/response wiring only, every
calculation delegated to backend/analytics/calculations.py (task ground
rule)."""
import os
import tempfile
from datetime import datetime

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.analytics.calculations import get_analytics_dashboard, get_funds_trend
from backend.db.models import AuditLog
from backend.db.session import SessionLocal
from backend.shared.enums import ChangeSource

from ..schemas.analytics import AnalyticsDashboardResponse, FundsTrendResponse

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard", response_model=AnalyticsDashboardResponse)
def get_dashboard():
    return get_analytics_dashboard()


@router.get("/funds-trend", response_model=FundsTrendResponse)
def get_trend():
    return {"trend": get_funds_trend()}


@router.get("/export")
def get_export():
    """Downloadable copy of the same table shown on screen — vendor name/
    ERP, outstanding, every month's payable/paid, aging buckets, oldest-
    bucket info. Brand-new workbook (same temp-file/FileResponse/
    BackgroundTask pattern as GET /models/5/finalized-plan-export), not
    related to EXCEL_PATH."""
    session = SessionLocal()
    try:
        data = get_analytics_dashboard(session=session)
        # Audit-log revamp (Task B) — every export, which type.
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=None,
                field_name="export",
                old_value=None,
                new_value="Vendor Analytics export",
                source=ChangeSource.EXPORT.value,
            )
        )
        session.commit()
    finally:
        session.close()

    months = data["months"]
    bucket_labels = list(data["aging_totals"].keys())
    rows = []
    for v in data["vendors"]:
        history_by_month = {h["month"]: h for h in v["history"]}
        row = {"Vendor Name": v["vendor_name"], "ERP Code": v["erp_code"], "Outstanding": v["outstanding_balance"]}
        for m in months:
            h = history_by_month.get(m)
            label = m.strftime("%b-%Y")
            row[f"{label} Payable"] = h["payable"] if h else 0.0
            row[f"{label} Paid"] = h["payment"] if h else 0.0
        for label in bucket_labels:
            row[label] = v["aging_buckets"].get(label, 0.0)
        row["Oldest Bucket"] = v["oldest_bucket"]
        row["Oldest Bucket Months Back"] = v["oldest_bucket_months_back"]
        row["Oldest Bucket Amount"] = v["oldest_bucket_amount"]
        rows.append(row)

    df = pd.DataFrame(rows)
    fd, tmp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    df.to_excel(tmp_path, index=False)

    filename = f"Vendor Analytics - {datetime.now():%d-%b-%Y %H%M%S}.xlsx"
    return FileResponse(
        tmp_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=BackgroundTask(os.remove, tmp_path),
    )
