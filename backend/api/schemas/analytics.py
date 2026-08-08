"""Request/response schemas for the Analytics dashboard router
(backend/analytics/calculations.py does the actual math — this is shape
only)."""
from datetime import date

from pydantic import BaseModel


class AnalyticsHistoryPoint(BaseModel):
    month: date
    payable: float
    payment: float


class AnalyticsMonthlyBreakdownRow(BaseModel):
    months_back: int
    month: date
    label: str
    amount: float  # still-owed balance (post-FIFO)
    payable: float
    payment: float


class AnalyticsVendor(BaseModel):
    vendor_id: int
    erp_code: str
    vendor_name: str
    outstanding_balance: float
    aging_buckets: dict[str, float]
    oldest_bucket: str | None = None
    oldest_bucket_months_back: int | None = None
    oldest_bucket_amount: float
    history: list[AnalyticsHistoryPoint]
    # aging.monthly_breakdown (backend/shared/aging.py) — oldest -> newest,
    # every calendar month incl. zero-balance ones. Drives the vendor detail
    # modal's per-month list, replacing the old 5-bucket tile summary.
    monthly_breakdown: list[AnalyticsMonthlyBreakdownRow]


class AnalyticsMonthAggregate(BaseModel):
    month: date
    total_payable: float
    total_payment: float
    cumulative_debt: float
    cumulative_paid: float


class AnalyticsDashboardResponse(BaseModel):
    vendors: list[AnalyticsVendor]
    months: list[date]
    aggregates: list[AnalyticsMonthAggregate]
    aging_totals: dict[str, float]


class FundsTrendPoint(BaseModel):
    month: date
    available_funds: float
    min_funds_required: float


class FundsTrendResponse(BaseModel):
    trend: list[FundsTrendPoint]
