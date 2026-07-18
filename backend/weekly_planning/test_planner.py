"""Tests against the real ingested database (backend/db/app.db) — no synthetic data."""
from datetime import date

import pytest

from backend.db.models import MonthlyLedger, PlanAllocation, Vendor
from backend.db.session import SessionLocal
from backend.models.model2_min_funds_advisory.advisor import (
    calculate_minimum_funds_required,
    generate_plan,
    required_amount,
)
from backend.shared.enums import VendorCategory
from backend.shared.payment_logging import log_payment
from backend.weekly_planning.calendar_utils import weeks_in_month
from backend.weekly_planning.planner import build_weekly_view


def test_weekly_numbers_correct_for_a_real_vendor():
    session = SessionLocal()
    try:
        plan = generate_plan(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]
        row = detail.iloc[0]
        plan_row = plan["allocations"][plan["allocations"]["vendor_id"] == row["vendor_id"]].iloc[0]
        assert row["actual_planned"] == plan_row["allocated_amount"]
        assert row["minimum_required"] == plan_row["required_amount"]
    finally:
        session.rollback()
        session.close()


def test_funding_exactly_minimum_matches_every_week():
    session = SessionLocal()
    try:
        total = calculate_minimum_funds_required(session=session)["total"]
        plan = generate_plan(available_funds=total, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        ws = view["weekly_summary"]
        assert (ws["minimum_required"] - ws["actual_planned"]).abs().max() < 1
    finally:
        session.rollback()
        session.close()


def test_funding_below_minimum_shows_shortfall_in_at_least_one_week():
    session = SessionLocal()
    try:
        total = calculate_minimum_funds_required(session=session)["total"]
        plan = generate_plan(available_funds=total * 0.5, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        ws = view["weekly_summary"]
        assert (ws["actual_planned"] < ws["minimum_required"] - 1).any()
    finally:
        session.rollback()
        session.close()


def test_within_week_order_follows_score_descending():
    session = SessionLocal()
    try:
        plan = generate_plan(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]
        for week, group in detail.groupby("assigned_week"):
            ordered = group.sort_values("within_week_order")
            assert list(ordered["score"]) == sorted(ordered["score"], reverse=True)
    finally:
        session.rollback()
        session.close()


def test_persisted_plan_allocations_match_detail():
    session = SessionLocal()
    try:
        plan = generate_plan(available_funds=100_000_000, session=session)
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
    """
    session = SessionLocal()
    try:
        tagged = session.query(Vendor).filter(Vendor.assigned_week.isnot(None)).order_by(Vendor.id).limit(2).all()
        assert len(tagged) == 2, "need at least 2 real vendors with an assigned_week to run this test"
        tagged[0].category = VendorCategory.MUST_PAY
        tagged[1].category = VendorCategory.COMMITMENT
        tagged[1].commitment_months = 2
        session.flush()

        plan = generate_plan(available_funds=1, session=session)  # severe shortfall
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        detail = view["detail"]

        for vendor in tagged:
            ledger_rows = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
            expected_amount, _ = required_amount(vendor, ledger_rows)
            row = detail[detail["vendor_id"] == vendor.id].iloc[0]
            assert row["actual_planned"] == pytest.approx(expected_amount, abs=1e-3)
            assert row["actual_planned"] == pytest.approx(row["minimum_required"], abs=1e-3)

        # Confirm the shortfall is real and actually shrinking Normal vendors
        # in the same run (otherwise this test wouldn't prove anything).
        normal_rows = detail[~detail["vendor_id"].isin([v.id for v in tagged])]
        assert (normal_rows["actual_planned"] < normal_rows["minimum_required"] - 1).any()
    finally:
        session.rollback()
        session.close()


# ---- week_distribution_plan / week_actual_paid (display-only) -------------


def test_week_distribution_plan_defaults_to_full_amount_in_assigned_week_when_persisted():
    session = SessionLocal()
    try:
        plan = generate_plan(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, model_used="model2", funds_figure=100_000_000)
        detail = view["detail"]
        assert not detail.empty
        for row in detail.itertuples():
            assert row.week_distribution_plan == {str(row.assigned_week): pytest.approx(row.actual_planned)}

        # Also correct on the real, persisted DB row — not just the
        # in-memory detail DataFrame this function returns.
        persisted = session.query(PlanAllocation).filter_by(plan_run_id=view["plan_run_id"]).limit(10).all()
        assert persisted
        for allocation in persisted:
            assert allocation.week_distribution_plan == {
                str(allocation.assigned_week): pytest.approx(float(allocation.allocated_amount))
            }
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

        plan = generate_plan(available_funds=100_000_000, session=session)
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
        plan = generate_plan(available_funds=100_000_000, session=session)
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

        plan = generate_plan(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, model_used="model2", funds_figure=100_000_000)
        row = view["detail"][view["detail"]["vendor_id"] == vendor.id].iloc[0]
        planned_before = row["week_distribution_plan"]
        assert planned_before == {"2": pytest.approx(row["actual_planned"])}

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

        plan = generate_plan(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)

        assert view["detail"].empty
        assert view["weekly_summary"].empty
        assert list(view["weekly_summary"].columns) == ["minimum_required", "actual_planned"]
        assert "week_distribution_plan" in view["detail"].columns
        assert "week_actual_paid" in view["detail"].columns
    finally:
        session.rollback()
        session.close()
