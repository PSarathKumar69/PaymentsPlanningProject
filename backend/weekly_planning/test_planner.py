"""Tests against the real ingested database (backend/db/app.db) — no synthetic data."""
from datetime import date

import pandas as pd
import pytest

from backend.db.models import MonthlyLedger, PlanAllocation, Vendor
from backend.db.session import SessionLocal
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import AllocationStatus, VendorCategory
from backend.shared.min_funds import (
    _load_vendors_and_ledger,
    _vendor_required_rows,
    calculate_minimum_funds_required,
)
from backend.shared.min_funds_v2 import required_amount_v2
from backend.models.new_model_2.allocator import _seed_and_read_buckets
from backend.shared.payment_logging import log_payment
from backend.weekly_planning.calendar_utils import week_for_date, weeks_in_month
from backend.weekly_planning.planner import build_weekly_view


def _generate_plan_for_test(available_funds, session=None, as_of=None, eligible_vendor_ids=None):
    """These tests exercise build_weekly_view() (model-agnostic — it only
    groups an already-computed allocations DataFrame by week, per its own
    docstring), not any one model's allocation algorithm. This is the old
    Model 2 (`backend/models/model2_min_funds_advisory/advisor.py`, deleted
    once New Model 2 became the sole model) `generate_plan()`, copied
    in-place so this file's tests keep the exact vehicle they were written
    against — every function it calls (`_load_vendors_and_ledger`,
    `_vendor_required_rows`) still lives in backend/shared/min_funds.py."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        if eligible_vendor_ids is not None:
            vendors = [v for v in vendors if v.id in eligible_vendor_ids]
        rows = _vendor_required_rows(vendors, ledger_by_vendor, as_of=as_of)

        guaranteed = [r for r in rows if r["category"] != VendorCategory.NORMAL.value]
        normal = [r for r in rows if r["category"] == VendorCategory.NORMAL.value]

        guaranteed_total = sum(r["required_amount"] for r in guaranteed)
        remaining = available_funds - guaranteed_total
        escalation = remaining < 0

        normal.sort(key=lambda r: (-r["oldest_bucket_age_days"], -r["required_amount"]))

        allocations = []
        for r in guaranteed:
            allocated = r["required_amount"]
            status = AllocationStatus.GUARANTEED.value
            if allocated > r["outstanding_balance"] + MONEY_EPSILON:
                allocated = max(r["outstanding_balance"], 0.0)
                status = AllocationStatus.PARTIAL.value
            allocations.append({**r, "allocated_amount": allocated, "status": status})

        funds_left = max(remaining, 0.0)
        for r in normal:
            if funds_left <= 0:
                allocations.append({**r, "allocated_amount": 0.0, "status": AllocationStatus.ZERO.value})
                continue
            if funds_left >= r["required_amount"]:
                allocated, status = r["required_amount"], AllocationStatus.FULL.value
            else:
                allocated, status = funds_left, AllocationStatus.PARTIAL.value
            if allocated > r["outstanding_balance"] + MONEY_EPSILON:
                allocated = max(r["outstanding_balance"], 0.0)
                status = (
                    AllocationStatus.FULL.value
                    if allocated >= r["required_amount"] - MONEY_EPSILON
                    else AllocationStatus.PARTIAL.value
                )
            funds_left -= allocated
            allocations.append({**r, "allocated_amount": allocated, "status": status})

        return {
            "available_funds": available_funds,
            "guaranteed_total": guaranteed_total,
            "escalation": escalation,
            "escalation_shortfall": max(-remaining, 0.0),
            "allocations": pd.DataFrame(allocations),
        }
    finally:
        if owns_session:
            session.close()


def test_weekly_numbers_correct_for_a_real_vendor():
    session = SessionLocal()
    try:
        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]
        row = detail.iloc[0]
        plan_row = plan["allocations"][plan["allocations"]["vendor_id"] == row["vendor_id"]].iloc[0]
        assert row["actual_planned"] == plan_row["allocated_amount"]

        # minimum_required is required_amount_v2() now (this fix) — check
        # against that directly. plan_row["required_amount"] is the test
        # helper's own OLD-rule figure (_vendor_required_rows, min_funds.py)
        # and is a different number from minimum_required by design now.
        vendor = session.query(Vendor).filter_by(id=int(row["vendor_id"])).one()
        ledger_rows = (
            session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
        )
        expected_amount, _ = required_amount_v2(vendor, ledger_rows)
        assert row["minimum_required"] == pytest.approx(expected_amount)
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize("erp_code", ["V00193", "V00204", "V00194", "V00393", "V00226"])
def test_weekly_view_minimum_required_matches_planning_table_for_known_vendors(erp_code):
    """The 5 real vendors that surfaced the vendors.py aging-endpoint bug
    (backend/api/test_api.py's own dedicated test) — build_weekly_view()'s
    own minimum_required must agree with the Planning table's
    required_amount_v2() figure for these too. (build_weekly_view() itself
    was already correct going into this task — see this task's report —
    this pins that down for the specific vendors that mattered.)
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code=erp_code).one()
        ledger_rows = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
        expected_amount, _ = required_amount_v2(vendor, ledger_rows)

        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]
        matches = detail[detail["vendor_id"] == vendor.id]
        if matches.empty:
            pytest.skip(f"{erp_code} has no assigned_week in the real sheet, not part of this cycle's plan")
        assert matches.iloc[0]["minimum_required"] == pytest.approx(expected_amount, abs=0.01)
    finally:
        session.rollback()
        session.close()


def test_funding_exactly_minimum_matches_every_week():
    """build_weekly_view()'s own minimum_required is required_amount_v2() now
    (this fix) — "exactly funding the minimum" has to mean the v2 total (the
    same number New Model 2's own Planning table already shows Finance,
    docs/14), not the OLD min_funds.py total `_generate_plan_for_test()`
    computes internally for its own (unchanged, still-old-rule) funding
    decisions. Reuse the helper for a real allocations DataFrame shaped
    right (same vendor set/columns), then overwrite allocated_amount with
    each vendor's real v2 required amount — so what got "fully funded" and
    what build_weekly_view calls "required" are the same number again.
    """
    session = SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        v2_amounts = {v.id: required_amount_v2(v, ledger_by_vendor[v.id])[0] for v in vendors}
        total = sum(v2_amounts.values())

        plan = _generate_plan_for_test(available_funds=total, session=session)
        plan["allocations"]["allocated_amount"] = plan["allocations"]["vendor_id"].map(v2_amounts)

        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        ws = view["weekly_summary"]
        assert (ws["minimum_required"] - ws["actual_planned"]).abs().max() < 1
    finally:
        session.rollback()
        session.close()


def test_funding_below_minimum_shows_shortfall_in_at_least_one_week():
    """Left on the OLD total/rule deliberately (not swapped to v2, unlike the
    "exactly minimum" test above) — this only asserts a real shortfall shows
    up SOMEWHERE (`.any()`), never an exact match. Funding at half of any
    reasonable required-amount total leaves at least one week short under
    either rule, so this stays a valid, rule-agnostic check of
    build_weekly_view()'s own shortfall arithmetic without needing to care
    which required-amount definition produced the funding target."""
    session = SessionLocal()
    try:
        total = calculate_minimum_funds_required(session=session)["total"]
        plan = _generate_plan_for_test(available_funds=total * 0.5, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        ws = view["weekly_summary"]
        assert (ws["actual_planned"] < ws["minimum_required"] - 1).any()
    finally:
        session.rollback()
        session.close()


def test_within_week_order_orders_by_priority_tag_then_score():
    """docs/06 confirmed rule: priority tag first (P0 Must Pay, P1
    Commitment, then the live DB bucket_order — P2-P5 today), score only
    breaking ties within the same tag. Checked against the real seeded
    vendor set directly (no synthetic tagging needed) — its real
    assigned_week tags already put more than one priority tag, including an
    untagged (None) vendor (id 9, week 4), in the same displayed week; see
    the report for confirmation this dataset actually exercises that.
    """
    session = SessionLocal()
    try:
        bucket_order, _, _, _, _ = _seed_and_read_buckets(session)
        tag_rank = {bucket: i for i, bucket in enumerate(bucket_order)}
        untagged_rank = len(tag_rank)  # same "dead last" default as planner.py itself

        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]

        weeks_with_more_than_one_tag = 0
        for week, group in detail.groupby("assigned_week"):
            ordered = group.sort_values("within_week_order")
            # Dense 1..N contract preserved (plan_history.py/vendors.py both
            # read this as a plain rank, not just a sort key).
            assert list(ordered["within_week_order"]) == list(range(1, len(ordered) + 1))

            ranks = [tag_rank.get(t, untagged_rank) for t in ordered["priority_tag"]]
            scores = list(ordered["score"])
            if len(set(ranks)) > 1:
                weeks_with_more_than_one_tag += 1
            for i in range(len(ordered) - 1):
                assert ranks[i] <= ranks[i + 1], f"week {week}: tag rank went backwards at position {i}"
                if ranks[i] == ranks[i + 1]:
                    assert scores[i] >= scores[i + 1], f"week {week}: score not descending within same tag at {i}"

        assert weeks_with_more_than_one_tag > 0, (
            "expected the real seeded dataset to have at least one week with more than one priority "
            "tag present — otherwise this test can't actually prove the tag-ordering half of the rule"
        )
    finally:
        session.rollback()
        session.close()


def test_persisted_plan_allocations_match_detail():
    session = SessionLocal()
    try:
        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, model_used="model2", funds_figure=100_000_000)
        persisted = (
            session.query(PlanAllocation).filter_by(plan_run_id=view["plan_run_id"]).order_by(PlanAllocation.vendor_id).all()
        )
        detail_by_vendor = view["detail"].set_index("vendor_id")
        assert len(persisted) == len(detail_by_vendor)
        for row in persisted:
            expected = detail_by_vendor.loc[row.vendor_id]
            assert row.assigned_week == expected["assigned_week"]
            assert row.within_week_order == expected["within_week_order"]
            assert float(row.allocated_amount) == pytest.approx(expected["actual_planned"], abs=1e-3)
    finally:
        session.rollback()
        session.close()


def test_weeks_in_month_4_and_5_week_real_months():
    assert weeks_in_month(2026, 2) == 4  # Feb-26 (non-leap, 28 days) — present in the real ledger
    assert weeks_in_month(2025, 4) == 5  # Apr-25 (30 days) — present in the real ledger


def test_weekly_view_inherits_must_pay_commitment_guarantee_under_severe_shortfall():
    """docs/06's new paragraph: the weekly view must never re-shrink what
    generate_plan() already guaranteed a Must Pay/Commitment vendor — it
    only groups. Tag real vendors, force a severe shortfall, and check the
    guaranteed vendor's full required_amount() lands untouched in their week.

    Root-cause fix (this task): this test used to compare `actual_planned`
    against a FRESH required_amount_v2() call — but `_generate_plan_for_test`
    just above is a deliberate, permanent stand-in for the retired old
    Model 2's generate_plan(), which computes a guaranteed vendor's amount
    via the OLD required_amount() rule (see its own docstring — every other
    test in this file relies on that same old-formula vehicle to test
    build_weekly_view()'s grouping logic in isolation from any one model's
    allocation math). For a real vendor whose old-vs-v2 required amount
    actually differs (confirmed here for Must Pay vendor V00400 — see
    backend/api/test_api.py's dedicated old-vs-v2 regression test for the
    5 vendors that surfaced this class of bug), that made this assertion
    compare two different formulas' outputs against each other, not test
    build_weekly_view() at all. The actual claim this test needs to prove —
    "build_weekly_view never shrinks what generate_plan already gave it" —
    is compare against `plan["allocations"]`'s own figures, whatever
    formula produced them, not an independently recomputed one.
    """
    session = SessionLocal()
    try:
        tagged = session.query(Vendor).filter(Vendor.assigned_week.isnot(None)).order_by(Vendor.id).limit(2).all()
        assert len(tagged) == 2, "need at least 2 real vendors with an assigned_week to run this test"
        tagged[0].category = VendorCategory.MUST_PAY
        tagged[1].category = VendorCategory.COMMITMENT
        tagged[1].commitment_months = 2
        session.flush()

        plan = _generate_plan_for_test(available_funds=1, session=session)  # severe shortfall
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]

        for vendor in tagged:
            plan_row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
            row = detail[detail["vendor_id"] == vendor.id].iloc[0]
            # Never shrunk relative to what generate_plan() actually handed
            # build_weekly_view() ...
            assert row["actual_planned"] == pytest.approx(plan_row["allocated_amount"], abs=1e-3)
            # ... and that allocation itself was the vendor's own full
            # required amount — the guarantee held under a near-zero-funds
            # shortfall, exactly as generate_plan() (whichever formula it
            # used) computed it.
            assert row["actual_planned"] == pytest.approx(plan_row["required_amount"], abs=1e-3)

        # Confirm the shortfall is real and actually shrinking Normal vendors
        # in the same run (otherwise this test wouldn't prove anything).
        normal_rows = detail[~detail["vendor_id"].isin([v.id for v in tagged])]
        assert (normal_rows["actual_planned"] < normal_rows["minimum_required"] - 1).any()
    finally:
        session.rollback()
        session.close()


# ---- week_distribution_plan / week_actual_paid (display-only) -------------


def test_week_distribution_plan_defaults_to_full_amount_in_assigned_week_when_persisted():
    """Defaults to the vendor's EFFECTIVE amount — their override, if the
    real DB already has one for this vendor this cycle, else the model's own
    suggested actual_planned figure. Some real vendors in the actual ledger
    already carry a live override (Finance set one in a prior real session),
    so this can't assume every vendor's override_amount is None."""
    session = SessionLocal()
    try:
        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, model_used="model2", funds_figure=100_000_000)
        detail = view["detail"]
        assert not detail.empty
        for row in detail.itertuples():
            vendor = session.query(Vendor).filter_by(id=row.vendor_id).one()
            if vendor.week_distribution_plan is not None:
                # A real vendor Finance has already manually split across
                # weeks in a prior real session — sticky, theirs alone
                # (docs/06), stamped as-is regardless of the current
                # override/suggestion.
                assert row.week_distribution_plan == vendor.week_distribution_plan
                continue
            # pandas stores a missing override as NaN, not None, once the
            # column holds a mix of real overrides and unset ones.
            expected = row.actual_planned if pd.isna(row.override_amount) else row.override_amount
            assert row.week_distribution_plan == {str(row.assigned_week): pytest.approx(expected)}

        # Also correct on the real, persisted DB row — not just the
        # in-memory detail DataFrame this function returns.
        persisted = session.query(PlanAllocation).filter_by(plan_run_id=view["plan_run_id"]).limit(10).all()
        assert persisted
        for allocation in persisted:
            vendor = session.query(Vendor).filter_by(id=allocation.vendor_id).one()
            if vendor.week_distribution_plan is not None:
                # A real vendor Finance has already manually split across
                # weeks in a prior real session — that sticky split is
                # theirs alone (docs/06) and is stamped as-is, regardless of
                # whether it still matches the current override/suggestion.
                assert allocation.week_distribution_plan == vendor.week_distribution_plan
                continue
            expected = (
                float(allocation.override_amount)
                if allocation.override_amount is not None
                else float(allocation.allocated_amount)
            )
            assert allocation.week_distribution_plan == {str(allocation.assigned_week): pytest.approx(expected)}
    finally:
        session.rollback()
        session.close()


def test_week_distribution_plan_default_reflects_override_not_stale_suggested_amount():
    """Bug fix (this task): Finance overrides a vendor's amount, then
    regenerates without ever manually touching a week (Vendor.
    week_distribution_plan stays None the whole time) — the fresh
    regeneration's default week bucket must seed from the EFFECTIVE
    (overridden) amount, not silently revert to the model's original
    suggested figure. Previously seeded from row.actual_planned
    unconditionally, so override + regenerate looked like the override
    never took effect in the Distribution column."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.assigned_week.isnot(None)).order_by(Vendor.id).first()
        assert vendor is not None

        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        plan_row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
        override_value = plan_row["allocated_amount"] + 12345.0
        vendor.override_amount = override_value
        session.flush()

        plan2 = _generate_plan_for_test(available_funds=100_000_000, session=session)
        # persist=False (like most other tests in this file): the bug and
        # its fix live entirely in how the default week_distribution_plan
        # dict is computed, identically on both the persist=True and
        # persist=False branches (see planner.py) — no need for a real,
        # heavier 422-row insert just to exercise it.
        view = build_weekly_view(plan2["allocations"], session=session, persist=False)
        detail = view["detail"]
        row = detail[detail["vendor_id"] == vendor.id].iloc[0]
        assert row["week_distribution_plan"] == {str(row["assigned_week"]): pytest.approx(override_value)}
    finally:
        session.rollback()
        session.close()


def test_week_distribution_plan_carries_forward_from_vendor_level_sticky_value():
    """docs/06 follow-up (plan-history feature): once Finance has edited a
    vendor's distribution (Vendor.week_distribution_plan gets set, e.g. via
    PATCH /plan-allocations/{id}/week-distribution), a fresh
    Generate/Regenerate must stamp that SAME sticky value onto the new row —
    never reset it back to the {assigned_week: allocated_amount} default.
    Real behavior change from before this feature (previously always reset
    fresh on every plan_run).
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.assigned_week.isnot(None)).order_by(Vendor.id).first()
        assert vendor is not None
        custom_distribution = {"1": 555.0, "3": 777.0}
        vendor.week_distribution_plan = custom_distribution
        session.flush()

        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, model_used="model2", funds_figure=100_000_000)
        detail = view["detail"]
        row = detail[detail["vendor_id"] == vendor.id].iloc[0]
        assert row["week_distribution_plan"] == custom_distribution  # carried forward, not reset

        persisted = (
            session.query(PlanAllocation).filter_by(plan_run_id=view["plan_run_id"], vendor_id=vendor.id).one()
        )
        assert persisted.week_distribution_plan == custom_distribution
    finally:
        session.rollback()
        session.close()


def test_week_distribution_plan_defaults_to_full_amount_when_not_persisted():
    session = SessionLocal()
    try:
        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]
        row = detail.iloc[0]
        assert row["week_distribution_plan"] == {str(row["assigned_week"]): pytest.approx(row["actual_planned"])}
        assert row["plan_allocation_id"] is None
    finally:
        session.rollback()
        session.close()


def test_week_actual_paid_reflects_a_real_payment_under_its_own_week():
    """Bug-adjacent guardrail: a payment logged in a different week than the
    plan must not change week_distribution_plan, and must show up under its
    OWN real week in week_actual_paid — a live aggregation, never the same
    thing as the planned figure."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.opening_balance > 500_000).order_by(Vendor.id).first()
        vendor.assigned_week = 2
        session.flush()

        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, model_used="model2", funds_figure=100_000_000)
        row = view["detail"][view["detail"]["vendor_id"] == vendor.id].iloc[0]
        planned_before = row["week_distribution_plan"]
        # Pulled-forward week fix: assigned_week=2 only survives as-is if
        # week 2 hasn't already elapsed relative to today's real date —
        # otherwise build_weekly_view() correctly pulls it forward to
        # today's real week (planner.py's own documented behavior).
        expected_week = str(max(2, week_for_date(date.today())))
        # Effective amount — this real vendor may already carry a live
        # override from a prior real session, same caveat as the dedicated
        # default-seeding test above.
        expected_amount = row["actual_planned"] if pd.isna(row["override_amount"]) else row["override_amount"]
        assert planned_before == {expected_week: pytest.approx(expected_amount)}

        log_payment(vendor.id, 12345.0, today=date(2026, 7, 22), session=session)  # July 22, 2026 -> week 4

        replan = build_weekly_view(plan["allocations"], session=session, persist=False)
        after_row = replan["detail"][replan["detail"]["vendor_id"] == vendor.id].iloc[0]
        assert after_row["week_actual_paid"] == {"4": 12345.0}
        # The planned figure (a separate, persisted row) is untouched by the payment.
        persisted = session.query(PlanAllocation).filter_by(id=int(row["plan_allocation_id"])).one()
        assert persisted.week_distribution_plan == planned_before
    finally:
        session.rollback()
        session.close()


def test_build_weekly_view_returns_cleanly_when_no_vendor_has_assigned_week():
    """Defensive fix: previously raised KeyError from a groupby("assigned_week")
    on a rows-less DataFrame with no such column, whenever no vendor in the DB
    carries an assigned_week tag."""
    session = SessionLocal()
    try:
        session.query(Vendor).update({Vendor.assigned_week: None})
        session.flush()

        plan = _generate_plan_for_test(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)

        assert view["detail"].empty
        assert view["weekly_summary"].empty
        assert list(view["weekly_summary"].columns) == ["minimum_required", "actual_planned"]
        assert "week_distribution_plan" in view["detail"].columns
        assert "week_actual_paid" in view["detail"].columns
    finally:
        session.rollback()
        session.close()
