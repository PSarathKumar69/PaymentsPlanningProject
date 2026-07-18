"""Weekly view of the initial monthly plan (docs/06-weekly-planning-regeneration.md).

None of Models 1/2/3 know about weeks — they produce one amount per vendor
for the whole month. This module only groups that already-computed result
by each vendor's existing `assigned_week` tag for display; it never
recomputes or re-divides funds per week.

Scoped to the initial monthly plan only — regeneration is a separate,
later prompt.
"""
from datetime import datetime

import pandas as pd

from backend.db.models import PlanAllocation, PlanRun
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import score_vendors
from backend.models.model2_min_funds_advisory.advisor import _load_vendors_and_ledger, required_amount
from backend.shared.payment_logging import week_actual_paid


def build_weekly_view(vendor_allocations, session=None, as_of=None, model_used=None, funds_figure=None, persist=True):
    """vendor_allocations: the `allocations` DataFrame from Model 2's or
    Model 3's generate_plan() — needs at least `vendor_id`/`allocated_amount`
    columns. (Model 1 has no generate_plan() of its own — see docs/03's
    guardrails — so there's nothing to pass in for a Model 1-only plan.)

    Returns a dict with:
      - weekly_summary: DataFrame indexed by week (1-5), columns
        minimum_required / actual_planned.
      - detail: one row per assigned vendor, incl. within_week_order.
      - plan_run_id: the persisted plan_runs.id (None if persist=False).
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        vendor_by_id = {v.id: v for v in vendors}
        allocated_by_vendor = dict(zip(vendor_allocations["vendor_id"], vendor_allocations["allocated_amount"]))
        scores = score_vendors(session=session, as_of=as_of).set_index("vendor_id")["score"]

        rows = []
        for vendor in vendors:
            if not vendor.assigned_week:  # None or 0 -> not part of this cycle's plan
                continue
            ledger_rows = ledger_by_vendor[vendor.id]
            minimum_required, rule = required_amount(vendor, ledger_rows, as_of=as_of)
            rows.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    "assigned_week": vendor.assigned_week,
                    "minimum_required": minimum_required,
                    "rule": rule,
                    "actual_planned": allocated_by_vendor.get(vendor.id, 0.0),
                    "score": scores.get(vendor.id, 0.0),
                    # Live, never stored redundantly — real payments already
                    # carry their own week; this only aggregates them fresh.
                    "week_actual_paid": week_actual_paid(session, vendor.id),
                }
            )
        detail = pd.DataFrame(rows)

        if detail.empty:
            # No vendor carries an assigned_week this cycle — nothing to
            # group by week. A rows-less DataFrame has no "assigned_week"
            # column at all, so groupby() on it would raise KeyError; return
            # an empty view instead of crashing.
            detail["within_week_order"] = pd.Series(dtype=int)
            detail["plan_allocation_id"] = pd.Series(dtype="Int64")
            detail["override_amount"] = pd.Series(dtype=float)
            detail["effective_amount"] = pd.Series(dtype=float)
            detail["week_distribution_plan"] = pd.Series(dtype=object)
            detail["week_actual_paid"] = pd.Series(dtype=object)
            weekly_summary = pd.DataFrame(columns=["minimum_required", "actual_planned"])
            weekly_summary.index.name = "assigned_week"
        else:
            # DEFAULT — confirm with Sarath: within-week execution order uses
            # Model 1's score, highest first (ties broken by original row order).
            detail["within_week_order"] = (
                detail.groupby("assigned_week")["score"].rank(method="first", ascending=False).astype(int)
            )
            detail = detail.sort_values(["assigned_week", "within_week_order"]).reset_index(drop=True)
            weekly_summary = detail.groupby("assigned_week")[["minimum_required", "actual_planned"]].sum()

        plan_run_id = None
        if persist:
            month = as_of or max(vendor_rows[-1].month for vendor_rows in ledger_by_vendor.values() if vendor_rows)
            plan_run = PlanRun(
                created_at=datetime.now(),
                month=month,
                model_used=model_used or "unspecified",
                funds_figure=funds_figure,
            )
            session.add(plan_run)
            session.flush()  # get plan_run.id
            if not detail.empty:
                new_allocations = []
                for row in detail.itertuples():
                    # Vendor.override_amount is now the sticky, per-vendor,
                    # whole-month source of truth (docs/06 fix) — a fresh
                    # plan/regeneration stamps whatever it currently is onto
                    # the new row directly, never NULL-by-default and never
                    # a "prior run" lookup.
                    vendor = vendor_by_id[row.vendor_id]
                    override_amount = vendor.override_amount
                    # Vendor.week_distribution_plan is now the same kind of
                    # sticky, per-vendor, whole-month source of truth (this
                    # task) — stamp whatever it currently is onto the fresh
                    # row directly. Only a brand-new vendor with no
                    # distribution edit yet at all this month (still None)
                    # falls back to the original default: the full suggested
                    # amount under this row's own assigned_week, every other
                    # week blank.
                    week_distribution_plan = (
                        vendor.week_distribution_plan
                        if vendor.week_distribution_plan is not None
                        else {str(row.assigned_week): row.actual_planned}
                    )
                    allocation = PlanAllocation(
                        plan_run_id=plan_run.id,
                        vendor_id=row.vendor_id,
                        assigned_week=row.assigned_week,
                        within_week_order=row.within_week_order,
                        allocated_amount=row.actual_planned,
                        override_amount=override_amount,
                        week_distribution_plan=week_distribution_plan,
                    )
                    session.add(allocation)
                    new_allocations.append(allocation)
                session.flush()  # get every allocation.id at once
                detail["plan_allocation_id"] = [a.id for a in new_allocations]
                detail["override_amount"] = [
                    float(a.override_amount) if a.override_amount is not None else None for a in new_allocations
                ]
                detail["effective_amount"] = detail["override_amount"].where(
                    detail["override_amount"].notna(), detail["actual_planned"]
                )
                detail["week_distribution_plan"] = [a.week_distribution_plan for a in new_allocations]
            plan_run_id = plan_run.id
            if owns_session:
                session.commit()
        elif not detail.empty:
            # Not persisted -> no real PlanAllocation row exists to attach an
            # override to; effective_amount is just this run's own figure.
            detail["plan_allocation_id"] = None
            detail["override_amount"] = None
            detail["effective_amount"] = detail["actual_planned"]
            # Not persisted -> no real row to stamp, but still reflect the
            # vendor's current sticky week_distribution_plan (if any) rather
            # than always showing the plain default, same convention as the
            # persist=True branch above.
            detail["week_distribution_plan"] = detail.apply(
                lambda r: (
                    vendor_by_id[r["vendor_id"]].week_distribution_plan
                    if vendor_by_id[r["vendor_id"]].week_distribution_plan is not None
                    else {str(r["assigned_week"]): r["actual_planned"]}
                ),
                axis=1,
            )

        return {"weekly_summary": weekly_summary, "detail": detail, "plan_run_id": plan_run_id}
    finally:
        if owns_session:
            session.close()
