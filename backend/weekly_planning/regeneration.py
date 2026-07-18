"""Regeneration engine (docs/06-weekly-planning-regeneration.md).

Regeneration re-runs the chosen model's generate_plan() across whichever
vendors are still eligible — not week by week (see docs/06's "how the
weekly split actually works"). The headroom cap below can claw back money
from a vendor already paid ahead of schedule; when it does, up to two
smaller generate_plan_fn() passes try to redistribute that reclaimed total
— first to any other capped guaranteed-tier vendor (Tier 1, structurally
always a no-op, see the comment at its call site), then to genuinely
underfunded Normal vendors (Tier 2, see _capped below) — and whatever
neither pass absorbs is reported back as unallocated_surplus rather than
silently disappearing. Eligibility filtering, that redistribution, and the
pulled-forward top-up's display week are the only regeneration-specific
logic here; the model run itself is reused unmodified (each model's own
generate_plan() already applies the same per-vendor headroom cap to its
guaranteed/P0-P1 tier, so a fresh Generate Plan on day one is protected
too, not just a regeneration).

Suggestion-only, like everything else in this codebase (CLAUDE.md rule 2):
this only produces a new suggested plan_run — it never logs a payment.
"""
from datetime import datetime

import pandas as pd

from backend.db.models import PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import score_vendors
from backend.models.model2_min_funds_advisory.advisor import _load_vendors_and_ledger
from backend.shared.enums import AllocationStatus, PaymentStatus, VendorCategory
from backend.shared.min_funds import has_outstanding_balance

_MONEY_EPSILON = 1.0  # rounding tolerance for money comparisons, matches aging.py/advisor.py/payment_logging.py


def determine_eligibility(vendor, current_week, reconsider_decisions):
    """Returns (eligible: bool, reason: str) per docs/06's eligibility table.

    REVISED (confirmed with Sarath, overrides docs/06's original "Assigned
    Week is current week or later" clause) — Assigned Week is Finance-facing
    scheduling only and must never gate eligibility:
      - NOT_PAID is always eligible, unconditionally, regardless of week.
      - PARTIAL is eligible only when Reconsider=Yes for THIS call.
      - PAID_IN_FULL stays excluded permanently either way.
    current_week is used only to label a PARTIAL vendor's Reconsider=Yes
    top-up as "pulled_forward" when its own assigned_week has already
    elapsed (display-only — the top-up still shows under the current week,
    docs/06's one exception to grouping by Assigned Week); it is never part
    of the include/exclude decision itself. Fix (confirmed with Sarath):
    "pulled_forward" means specifically a top-up on an existing partial
    payment (docs/06) — a NOT_PAID vendor whose week has simply elapsed is
    always "normal", never "pulled_forward", since nothing's been paid to
    top up.

    reconsider_decisions: {vendor_id: bool} — Finance's fresh choices for
    THIS regeneration call. A vendor needing a decision but absent from this
    dict is treated as Reconsider=No (locked) — "never defaulted" means
    never inferring a stale Yes from a prior cycle, not that an explicit
    Yes is optional.
    """
    if not vendor.assigned_week:  # None/0 -> never part of any cycle's plan (same convention as planner.py)
        return False, "not_assigned_this_cycle"

    # docs/06 fix: takes priority over every payment_status branch below —
    # a vendor could reach zero outstanding while still labeled something
    # other than PAID_IN_FULL (payment_logging.py's own bug), so this must
    # not rely on the status label at all.
    if not has_outstanding_balance(vendor):
        return False, "no_outstanding_balance"

    if vendor.payment_status == PaymentStatus.PAID_IN_FULL:
        return False, "paid_in_full_excluded"

    if vendor.payment_status == PaymentStatus.NOT_PAID:
        return True, "normal"  # never a top-up (nothing paid yet) -> never pulled_forward, regardless of week

    # PARTIAL — only this specific call's Reconsider decision counts
    included = bool(reconsider_decisions.get(vendor.id, False))
    if not included:
        return False, "locked"

    # A genuine top-up on an existing partial payment (docs/06's precise
    # "pulled forward" definition) — never just "week has elapsed."
    reason = "pulled_forward" if vendor.assigned_week < current_week else "normal"
    return True, reason


def regenerate(generate_plan_fn, available_funds, current_week, reconsider_decisions=None, session=None, model_used=None):
    """generate_plan_fn: Model 2's or Model 3's generate_plan — called as
    generate_plan_fn(available_funds, session=session), same signature as
    the initial-plan flow.

    Returns a dict: weekly_summary-less detail (this is a re-plan, not the
    two-number weekly view), allocations DataFrame, plan_run_id, and which
    vendors were excluded/locked/pulled-forward for transparency.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        reconsider_decisions = reconsider_decisions or {}
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        vendor_by_id = {v.id: v for v in vendors}

        # Write Finance's fresh decisions now — DB-only, never historized,
        # never carried forward as a default (docs/06's guardrails).
        for vendor_id, decision in reconsider_decisions.items():
            vendor_by_id[vendor_id].reconsider = decision
        session.flush()

        eligibility = {v.id: determine_eligibility(v, current_week, reconsider_decisions) for v in vendors}
        eligible_ids = {vid for vid, (ok, _) in eligibility.items() if ok}
        pulled_forward_ids = {vid for vid, (ok, reason) in eligibility.items() if ok and reason == "pulled_forward"}

        # Re-run the chosen model ONCE, scoped to the eligible set from the
        # start via its eligible_vendor_ids filter — an excluded vendor must
        # never be part of the model's internal funds math (docs/06's fix:
        # computing them and discarding afterward would still let them
        # silently consume part of the funds pool).
        full_plan = generate_plan_fn(available_funds, session=session, eligible_vendor_ids=eligible_ids)
        eligible = full_plan["allocations"].copy()

        # Ledger integrity: a pulled-forward vendor's new top-up plus what
        # they were already paid this month must never exceed what's owed.
        # The model doesn't know about paid_so_far_this_month, so cap here.
        def _capped(row):
            vendor = vendor_by_id[row["vendor_id"]]
            headroom = float(vendor.opening_balance) - float(vendor.paid_so_far_this_month)
            return max(0.0, min(row["allocated_amount"], headroom))

        pre_cap_status = eligible["status"]
        eligible["allocated_amount"] = eligible.apply(_capped, axis=1)

        # Money the cap above claws back must not simply vanish — hand it to
        # other still-eligible vendors the model itself already marked as
        # underfunded by a real funds shortfall (status partial/zero), not to
        # a vendor the cap just shrank. Reuse generate_plan_fn for the
        # redistribution pass itself (same priority order, same rules)
        # instead of hand-rolling a second allocator.
        was_capped = full_plan["allocations"]["allocated_amount"] > eligible["allocated_amount"] + _MONEY_EPSILON
        reclaimed_total = (full_plan["allocations"]["allocated_amount"] - eligible["allocated_amount"]).sum()

        # .apply (scalar, elementwise), not a bare Series != : category may
        # be a plain str (Model 1/2's own rows) or the raw VendorCategory
        # enum member (Model 3's assign_buckets() output) — scalar
        # comparison is safe either way (str-Enum equality), matching this
        # codebase's existing convention of avoiding vectorized comparisons
        # against a mixed enum/str column (see bucketer.py's own note on
        # pandas' strict type(x) is str check in .isin()).
        is_guaranteed_tier = eligible["category"].apply(lambda c: c != VendorCategory.NORMAL.value)
        underfunded_mask = pre_cap_status.isin([AllocationStatus.PARTIAL.value, AllocationStatus.ZERO.value]) & ~was_capped
        underfunded_guaranteed_ids = set(eligible.loc[underfunded_mask & is_guaranteed_tier, "vendor_id"])
        underfunded_normal_ids = set(eligible.loc[underfunded_mask & ~is_guaranteed_tier, "vendor_id"])

        remaining_to_redistribute = reclaimed_total

        # Tier 1 — other capped guaranteed-tier (P0/P1) vendors in this same
        # round. CONFIRMED STRUCTURALLY UNREACHABLE, kept only for defensive
        # completeness: a guaranteed vendor's own allocation is always
        # min(required_amount, headroom) — entirely independent of the
        # funds pool (CLAUDE.md rule 5) — so a vendor already capped here is
        # already sitting at the absolute ceiling they can legally receive;
        # reclaiming money from vendor A creates no additional headroom for
        # vendor B. generate_plan_fn returns an ABSOLUTE entitlement (not an
        # incremental top-up) for guaranteed-tier rows, so adding it on top
        # would double-count — but the _capped() re-application right after
        # clamps any such inflation straight back down to the same headroom
        # ceiling, so this is safe even though it can never actually move
        # money in practice.
        if remaining_to_redistribute > _MONEY_EPSILON and underfunded_guaranteed_ids:
            before = eligible["allocated_amount"].sum()
            topup_plan = generate_plan_fn(
                remaining_to_redistribute, session=session, eligible_vendor_ids=underfunded_guaranteed_ids
            )
            topup_by_vendor = topup_plan["allocations"].set_index("vendor_id")["allocated_amount"]
            eligible["allocated_amount"] = eligible["allocated_amount"] + eligible["vendor_id"].map(topup_by_vendor).fillna(
                0.0
            )
            eligible["allocated_amount"] = eligible.apply(_capped, axis=1)  # re-cap the combined total
            remaining_to_redistribute -= max(eligible["allocated_amount"].sum() - before, 0.0)

        # Tier 2 — Normal vendors the model itself already marked
        # underfunded by a real funds shortfall (status partial/zero
        # pre-cap), not by the cap that just ran. Unchanged from before,
        # just now scoped to whatever Tier 1 didn't (structurally can't)
        # absorb.
        if remaining_to_redistribute > _MONEY_EPSILON and underfunded_normal_ids:
            before = eligible["allocated_amount"].sum()
            topup_plan = generate_plan_fn(
                remaining_to_redistribute, session=session, eligible_vendor_ids=underfunded_normal_ids
            )
            topup_by_vendor = topup_plan["allocations"].set_index("vendor_id")["allocated_amount"]
            eligible["allocated_amount"] = eligible["allocated_amount"] + eligible["vendor_id"].map(topup_by_vendor).fillna(
                0.0
            )
            eligible["allocated_amount"] = eligible.apply(_capped, axis=1)  # re-cap the combined total
            remaining_to_redistribute -= max(eligible["allocated_amount"].sum() - before, 0.0)

        # Whatever's left after both tiers (including "nothing to
        # redistribute to at all") must be reported, not silently dropped —
        # the funds-pool total stays correct either way, but Finance
        # previously had no visibility into money that went unaccounted for.
        unallocated_surplus = max(remaining_to_redistribute, 0.0)

        # Status/label must reflect the final, post-redistribution amount —
        # recomputed here, never left at the model's original pre-redistribution
        # label. Compares every row's final allocated_amount against
        # required_amount directly, regardless of original tier — a
        # guaranteed-tier (P0/P1) row that ended up short (headroom cap) is
        # PARTIAL, never silently left GUARANTEED (CLAUDE.md rule 5's whole
        # point: never let "guaranteed" mean "got less than promised").
        def _relabel(row):
            # .value (plain str), not the enum member: this feeds into a
            # DataFrame column, and pandas' string dtype does a strict
            # type(x) is str check in vectorized comparisons downstream.
            need = row["required_amount"] if pd.notna(row.get("required_amount")) else row["outstanding_balance"]
            if row["allocated_amount"] <= _MONEY_EPSILON:
                return AllocationStatus.ZERO.value
            if row["allocated_amount"] < need - _MONEY_EPSILON:
                return AllocationStatus.PARTIAL.value
            if row["category"] != VendorCategory.NORMAL.value:  # scalar comparison — safe, see is_guaranteed_tier above
                return AllocationStatus.GUARANTEED.value
            return AllocationStatus.FULL.value

        eligible["status"] = eligible.apply(_relabel, axis=1)

        # Pulled-forward vendors' assigned_week tag never changes (Finance-
        # owned data) — but this top-up displays under the current week,
        # the one documented exception to grouping by assigned_week.
        eligible["display_week"] = eligible["vendor_id"].apply(
            lambda vid: current_week if vid in pulled_forward_ids else vendor_by_id[vid].assigned_week
        )

        # DEFAULT — confirm with Sarath: within-week execution order uses
        # Model 1's score, highest first — same convention as the initial
        # plan's weekly view, and explicitly no jump-the-queue priority for
        # pulled-forward vendors (docs/06).
        scores = score_vendors(session=session).set_index("vendor_id")["score"]
        eligible["score"] = eligible["vendor_id"].map(scores)
        eligible["within_week_order"] = (
            eligible.groupby("display_week")["score"].rank(method="first", ascending=False).astype(int)
        )
        eligible = eligible.sort_values(["display_week", "within_week_order"]).reset_index(drop=True)

        month = max(vendor_rows[-1].month for vendor_rows in ledger_by_vendor.values() if vendor_rows)
        plan_run = PlanRun(
            created_at=datetime.now(),
            month=month,
            model_used=model_used or "unspecified_regeneration",
            funds_figure=available_funds,
        )
        session.add(plan_run)
        session.flush()
        for row in eligible.itertuples():
            vendor = vendor_by_id[row.vendor_id]
            # Vendor.week_distribution_plan is now the same kind of sticky,
            # per-vendor, whole-month source of truth override_amount
            # already is (this task) — stamp whatever it currently is onto
            # the fresh row directly. Only a vendor with no distribution
            # edit yet at all this month (still None) falls back to the
            # original default: the (re-)allocated amount under this row's
            # own display_week (current week for a pulled-forward top-up),
            # every other week blank.
            week_distribution_plan = (
                vendor.week_distribution_plan
                if vendor.week_distribution_plan is not None
                else {str(row.display_week): row.allocated_amount}
            )
            session.add(
                PlanAllocation(
                    plan_run_id=plan_run.id,
                    vendor_id=row.vendor_id,
                    assigned_week=row.display_week,
                    within_week_order=row.within_week_order,
                    allocated_amount=row.allocated_amount,
                    # Vendor.override_amount is now the sticky, per-vendor,
                    # whole-month source of truth (docs/06 fix) — stamped
                    # onto every fresh row directly, no "prior run" lookup.
                    override_amount=vendor.override_amount,
                    week_distribution_plan=week_distribution_plan,
                )
            )
        if owns_session:
            session.commit()

        return {
            "plan_run_id": plan_run.id,
            "allocations": eligible,
            "excluded": {vid: reason for vid, (ok, reason) in eligibility.items() if not ok},
            "pulled_forward": sorted(pulled_forward_ids),
            "unallocated_surplus": unallocated_surplus,
        }
    finally:
        if owns_session:
            session.close()
