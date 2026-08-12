"""Weekly view of the initial monthly plan (docs/06-weekly-planning-regeneration.md).

New Model 2's generate_plan() produces one amount per vendor for the whole
month — it knows nothing about weeks. This module only groups that
already-computed result by each vendor's existing `assigned_week` tag for
display; it never recomputes or re-divides funds per week.

Scoped to the initial monthly plan only — regeneration is a separate,
later prompt.

Pulled-forward week: a vendor's own `assigned_week` tag (Vendor.assigned_week)
is Finance-owned and never changed here. But the week a vendor's row
actually GROUPS/DISPLAYS under this round is `max(assigned_week,
current_week)` — a vendor whose week has already elapsed shows under the
current week instead of a past one nobody's tracking anymore. New Model 2
has no separate regeneration step (every "Generate"/"Regenerate" click goes
through the same generate-plan-and-weekly-view path — see new_model_2.py),
so every call through this function needs the same behavior, first
generation or fifth. A vendor whose week hasn't arrived yet is untouched
(assigned_week > current_week keeps them at their own future week).
`current_week` is derived the same way log_payment() already derives a
real payment's week — today's real date, not the (possibly stale) ledger
month — so it matches exactly what "today" means everywhere else a week
gets assigned in this codebase.

Bug fix (this task): the pull-forward above only makes sense when the plan
being built is actually FOR the real current calendar month. New Model 2's
planning month is Finance-picked and can genuinely be a future month
(docs/14, forward planning) — pulling every W1-W3 vendor of an August plan
forward to "week 4" just because today happens to be July's 4th week is
meaningless (the two months' weeks don't correspond) and corrupts that
plan's weekly grouping/summary. `planning_month` (new, optional — None
preserves every existing caller's exact behavior unchanged) is the actual
month Finance picked; the pull-forward only applies when it matches
today's real year/month.
"""
from datetime import date, datetime

import pandas as pd

from backend.db.models import PlanAllocation, PlanRun
from backend.db.session import SessionLocal
from backend.models.new_model_2.allocator import _seed_and_read_buckets
from backend.shared.scoring import score_vendors
from backend.shared.min_funds import _load_vendors_and_ledger
from backend.shared.min_funds_v2 import required_amount_v2
from backend.shared.payment_logging import week_actual_paid_all_vendors
from backend.weekly_planning.calendar_utils import week_for_date


def build_weekly_view(
    vendor_allocations, session=None, as_of=None, model_used=None, funds_figure=None, persist=True, planning_month=None
):
    """vendor_allocations: the `allocations` DataFrame from New Model 2's
    generate_plan() — needs at least `vendor_id`/`allocated_amount` columns.

    Returns a dict with:
      - weekly_summary: DataFrame indexed by week (1-5), columns
        minimum_required / actual_planned.
      - detail: one row per assigned vendor, incl. within_week_order. Its
        own "assigned_week" column is the pulled-forward DISPLAY week for
        this round (see module docstring) — Vendor.assigned_week itself,
        read elsewhere (e.g. the Priority column), is never touched.
      - plan_run_id: the persisted plan_runs.id (None if persist=False).
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        vendor_by_id = {v.id: v for v in vendors}
        allocated_by_vendor = dict(zip(vendor_allocations["vendor_id"], vendor_allocations["allocated_amount"]))
        scores = score_vendors(session=session, as_of=as_of).set_index("vendor_id")["score"]
        # Within-week execution order rank (docs/06, confirmed with Sarath):
        # P0 (Must Pay) first, P1 (Commitment) second, then the real,
        # DB-backed bucket_order (P2-P5 today) in its own live sequence —
        # never a hardcoded P2-P5 list (CLAUDE.md rule 7).
        #
        # P0/P1-ceiling task: bucket_order itself now ALSO includes P0/P1
        # (pinned at its own front, allocator.py) since they're real
        # PriorityBucket rows now — filtered back out here so this
        # function's own explicit P0:0/P1:1 seed below isn't overwritten by
        # the enumerate() loop re-numbering them into the P2+ range.
        #
        # Crash fix (prod outage, 2026-08-12): this used to import a
        # hardcoded PINNED_BUCKET_KEYS = ("P0", "P1") constant from
        # allocator.py — removed from that module by the pinned-role task
        # (2026-08-07), which replaced it with a DB-backed pinned_role
        # column so a Finance rename of the P0/P1 rows doesn't break
        # guaranteed-tier detection. This file was never updated to match,
        # so every import of this module (i.e. every request, since
        # main.py's router registration pulls this in transitively) raised
        # ImportError and took the whole app down. Fixed the same way
        # allocator.py's own generate_plan() derives it now — read
        # pinned_role fresh from _seed_and_read_buckets() every call, never
        # a hardcoded literal.
        bucket_order, _, _, _, pinned_role = _seed_and_read_buckets(session)
        pinned_bucket_keys = {key for key, role in pinned_role.items() if role}
        tag_rank = {"P0": 0, "P1": 1}
        for i, bucket in enumerate(b for b in bucket_order if b not in pinned_bucket_keys):
            tag_rank[bucket] = i + 2
        # Undecided edge case, flagged (house style, see allocator.py's own
        # FALLBACK_BUCKET comment): a vendor can carry no priority_tag at all
        # (allocator.py's own defensive-fallback comment confirms this
        # happens). docs/06's confirmation doesn't say what to do with one —
        # ranked one past every real tag, i.e. dead last within their week,
        # rather than erroring or defaulting to first/highest.
        untagged_rank = len(tag_rank)
        today = date.today()
        # planning_month=None (every pre-existing caller) keeps the exact
        # prior behavior — always pull forward against today's real week.
        # A caller that DOES pass planning_month only pulls forward when
        # it's genuinely today's real month; a future-planned month is
        # left exactly as Finance assigned it (see module docstring).
        is_current_real_month = planning_month is None or (
            (planning_month.year, planning_month.month) == (today.year, today.month)
        )
        current_week = week_for_date(today) if is_current_real_month else 0

        # Perf fix (prod bug, confirmed 2026-08: "Generate Plan" taking
        # 10+ seconds): computed ONCE for every vendor here instead of
        # calling week_actual_paid(session, vendor.id) inside the loop
        # below — that per-vendor version re-ran its own cycle-month
        # lookup query too, so this was up to 2 extra Neon round trips per
        # vendor (~900 for 450 vendors) before this fix.
        week_actual_paid_by_vendor = week_actual_paid_all_vendors(session)

        rows = []
        for vendor in vendors:
            if not vendor.assigned_week:  # None or 0 -> not part of this cycle's plan
                continue
            ledger_rows = ledger_by_vendor[vendor.id]
            minimum_required, rule = required_amount_v2(vendor, ledger_rows, as_of=as_of)
            rows.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    # Pulled forward only if their own week already elapsed —
                    # see module docstring. Untouched (stays at their own
                    # future week) otherwise.
                    "assigned_week": max(vendor.assigned_week, current_week),
                    "minimum_required": minimum_required,
                    "rule": rule,
                    "priority_tag": vendor.priority_tag,
                    "actual_planned": allocated_by_vendor.get(vendor.id, 0.0),
                    "score": scores.get(vendor.id, 0.0),
                    # Live, never stored redundantly — real payments already
                    # carry their own week; this only aggregates them fresh
                    # (batched above, not per-vendor — see the perf-fix
                    # comment there).
                    "week_actual_paid": week_actual_paid_by_vendor.get(vendor.id, {}),
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
            # CONFIRMED (docs/06): within-week execution order is priority
            # tag first (P0/P1, then the live bucket_order), score only
            # breaking ties within the same tag — no longer pure score.
            detail["_tag_rank"] = detail["priority_tag"].map(tag_rank).fillna(untagged_rank).astype(int)
            # kind="mergesort": stable, so vendors tied on both tag and score
            # still break by original row order, same tiebreak the old
            # score-only rank() gave via method="first".
            detail = detail.sort_values(
                ["assigned_week", "_tag_rank", "score"], ascending=[True, True, False], kind="mergesort"
            ).reset_index(drop=True)
            # Dense 1..N per week (same contract as before — plan_history.py
            # and vendors.py both read this as a plain rank, not a sort key).
            detail["within_week_order"] = detail.groupby("assigned_week").cumcount() + 1
            detail = detail.drop(columns="_tag_rank")
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
                    # falls back to the original default: the full EFFECTIVE
                    # amount (override, if Finance has set one, else the
                    # model's own suggested figure) under this row's own
                    # assigned_week, every other week blank. Deliberately NOT
                    # written back onto the vendor here — see plan_history.py's
                    # build_plan_run_history_response() for why (this
                    # per-row default must keep re-deriving fresh every
                    # regeneration for as long as Finance hasn't actually
                    # edited a week; freezing it here the first time it's
                    # computed would stop it from following a later
                    # assigned_week/amount change).
                    #
                    # Bug fix: this used to seed from row.actual_planned (the
                    # raw suggested amount) unconditionally, so a vendor who
                    # already had an override BEFORE this regeneration still
                    # got the week bucket reseeded to their pre-override
                    # suggested figure — override+regenerate silently
                    # reverted the weekly amount back to the old number.
                    # float(...): Vendor.override_amount is a Numeric column,
                    # which SQLAlchemy loads back as decimal.Decimal (not
                    # float) whenever a vendor already has a real, persisted
                    # override — json (week_distribution_plan's column type)
                    # can't serialize Decimal, so this would otherwise crash
                    # the very next regeneration for any vendor with an
                    # existing override.
                    effective_amount = float(override_amount) if override_amount is not None else row.actual_planned
                    week_distribution_plan = (
                        vendor.week_distribution_plan
                        if vendor.week_distribution_plan is not None
                        else {str(row.assigned_week): effective_amount}
                    )
                    allocation = PlanAllocation(
                        plan_run_id=plan_run.id,
                        vendor_id=row.vendor_id,
                        assigned_week=row.assigned_week,
                        within_week_order=row.within_week_order,
                        allocated_amount=row.actual_planned,
                        override_amount=override_amount,
                        week_distribution_plan=week_distribution_plan,
                        # Frozen denominator (this task's fix) — the same
                        # required_amount_v2() call already made a few lines
                        # up for this row, not recomputed.
                        required_amount_snapshot=row.minimum_required,
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
            # persist=True branch above — including the same effective-
            # amount (override, if set, else suggested) fix.
            detail["week_distribution_plan"] = detail.apply(
                lambda r: (
                    vendor_by_id[r["vendor_id"]].week_distribution_plan
                    if vendor_by_id[r["vendor_id"]].week_distribution_plan is not None
                    else {
                        str(r["assigned_week"]): (
                            float(vendor_by_id[r["vendor_id"]].override_amount)
                            if vendor_by_id[r["vendor_id"]].override_amount is not None
                            else r["actual_planned"]
                        )
                    }
                ),
                axis=1,
            )

        return {"weekly_summary": weekly_summary, "detail": detail, "plan_run_id": plan_run_id}
    finally:
        if owns_session:
            session.close()
