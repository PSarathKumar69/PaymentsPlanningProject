"""Analytics dashboard calculations — the ONLY place Analytics-tab arithmetic
lives (task ground rule: backend/api/routers/analytics.py is wiring only).

Every figure here is either read straight off the DB or delegated to the
same shared functions the rest of the app already uses —
compute_vendor_aging() (backend/shared/aging.py) for buckets/oldest-tranche,
live_outstanding_balance() (backend/shared/min_funds.py) for "what's still
owed" — never a second copy of that math. New Model 2's own Minimum Funds
Required total (required_amount_v2()) is summed by
calculate_minimum_funds_required_v2() at Generate Plan time instead (see
backend/api/routers/new_model_2.py) and simply read back here off
PlanRun.min_funds_required — this module never recomputes it.

Three decisions this module builds to exactly (Analytics Step 2 task):
  1. Funds trend only shows real PlanRun rows — no invented/backfilled point
     for a month with no real Generate Plan run.
  2. The most recent month in MonthlyLedger is the "open" cycle — its Paid
     figure is the live vendor.paid_so_far_this_month, not the (possibly
     stale) MonthlyLedger.payment. Every earlier month uses the ledger value
     as recorded.
  3. No Liquidity Health scoring here — that badge was dropped from the UI
     entirely, not reimplemented.
"""
from collections import defaultdict
from datetime import date

from backend.db.models import MonthlyLedger, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.ingestion import column_mapping_store
from backend.shared.aging import AGING_BUCKETS, compute_vendor_aging
from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL
from backend.shared.min_funds import live_outstanding_balance

_EMPTY_AGING_TOTALS = {label: 0.0 for label, _, _ in AGING_BUCKETS}


def _month_range(start: date, end: date) -> list[date]:
    """Every first-of-month date from start to end, inclusive."""
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def get_analytics_dashboard(session=None):
    """Per-vendor list + cross-vendor aggregates + aging totals for the
    Analytics dashboard's KPI cards, aging visual, charts, and vendor table.

    months is dynamic — sheet_start_month (column_mapping_store, CLAUDE.md
    rule 7) through the latest month actually recorded in MonthlyLedger,
    never a fixed count. Every active vendor is included regardless of
    outstanding balance (unlike backend/shared/min_funds.py's
    _load_vendors_and_ledger, which excludes zero-balance vendors for
    planning purposes — Analytics is a historical view, not a planning one).
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors = session.query(Vendor).filter(Vendor.is_active.isnot(False)).order_by(Vendor.id).all()
        if not vendors:
            return {"vendors": [], "months": [], "aggregates": [], "aging_totals": dict(_EMPTY_AGING_TOTALS)}

        vendor_ids = [v.id for v in vendors]
        ledger_rows = (
            session.query(MonthlyLedger)
            .filter(MonthlyLedger.vendor_id.in_(vendor_ids))
            .order_by(MonthlyLedger.vendor_id, MonthlyLedger.month)
            .all()
        )
        if not ledger_rows:
            return {"vendors": [], "months": [], "aggregates": [], "aging_totals": dict(_EMPTY_AGING_TOTALS)}

        ledger_by_vendor = defaultdict(list)
        for row in ledger_rows:
            ledger_by_vendor[row.vendor_id].append(row)

        latest_month = max(row.month for row in ledger_rows)
        sheet_start_month = column_mapping_store.get_sheet_start_month(session)
        months = _month_range(sheet_start_month, latest_month)

        aging_totals = dict(_EMPTY_AGING_TOTALS)
        month_payable_totals = {m: 0.0 for m in months}
        month_payment_totals = {m: 0.0 for m in months}
        vendor_payload = []

        for vendor in vendors:
            rows = ledger_by_vendor.get(vendor.id, [])
            paid_so_far = float(vendor.paid_so_far_this_month)
            aging = compute_vendor_aging(rows, as_of=latest_month, already_paid_this_cycle=paid_so_far)
            for label, amount in aging.bucket_balances.items():
                aging_totals[label] += amount

            by_month = {row.month: row for row in rows}
            history = []
            for m in months:
                row = by_month.get(m)
                payable = float(row.payable) if row else 0.0
                # Decision #2: the open (latest) month's Paid figure is the
                # live paid_so_far_this_month, not MonthlyLedger.payment,
                # which stays stale until the next Excel upload/rollover.
                payment = paid_so_far if m == latest_month else (float(row.payment) if row else 0.0)
                history.append({"month": m, "payable": payable, "payment": payment})
                month_payable_totals[m] += payable
                month_payment_totals[m] += payment

            vendor_payload.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    "outstanding_balance": live_outstanding_balance(vendor),
                    "aging_buckets": aging.bucket_balances,
                    "oldest_bucket": aging.oldest_bucket,
                    "oldest_bucket_months_back": aging.oldest_bucket_months_back,
                    "oldest_bucket_amount": aging.oldest_bucket_amount,
                    "history": history,
                    "monthly_breakdown": aging.monthly_breakdown,
                }
            )

        aggregates = []
        cumulative_payable = 0.0
        cumulative_payment = 0.0
        for m in months:
            cumulative_payable += month_payable_totals[m]
            cumulative_payment += month_payment_totals[m]
            aggregates.append(
                {
                    "month": m,
                    "total_payable": month_payable_totals[m],
                    "total_payment": month_payment_totals[m],
                    "cumulative_debt": cumulative_payable - cumulative_payment,
                    "cumulative_paid": cumulative_payment,
                }
            )

        return {"vendors": vendor_payload, "months": months, "aggregates": aggregates, "aging_totals": aging_totals}
    finally:
        if owns_session:
            session.close()


def get_funds_trend(session=None):
    """Decision #1: only real PlanRun rows with BOTH funds_figure and
    min_funds_required populated — nothing invented for a month with no
    real Generate Plan run. Scoped to New Model 2's own plan_run family
    (NEW_MODEL_2_PLAN_RUN_LABEL) — the only model that records
    min_funds_required at all. One point per month: if a month was
    regenerated more than once, the most recently created run for that
    month wins."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        rows = (
            session.query(PlanRun)
            .filter(PlanRun.model_used == NEW_MODEL_2_PLAN_RUN_LABEL)
            .filter(PlanRun.funds_figure.isnot(None))
            .filter(PlanRun.min_funds_required.isnot(None))
            .order_by(PlanRun.month, PlanRun.created_at)
            .all()
        )
        latest_by_month = {}
        for row in rows:
            latest_by_month[row.month] = row  # ascending created_at -> last write per month wins
        return [
            {
                "month": m,
                "available_funds": float(latest_by_month[m].funds_figure),
                "min_funds_required": float(latest_by_month[m].min_funds_required),
            }
            for m in sorted(latest_by_month)
        ]
    finally:
        if owns_session:
            session.close()
