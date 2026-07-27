"""Tests against the real ingested database (backend/db/app.db) — no synthetic data,
except for the vendors themselves (see _unallocated_vendor() below) and
whatever plan_allocation a test explicitly constructs to control "this
cycle's plan allocation" deterministically (noted per-test); always rolled
back, never persisted to app.db.

Vendors used for cycle-allocation-dependent scenarios are constructed fresh
per test via _unallocated_vendor() — guaranteed zero PlanAllocation/Payment
history by construction, not by luck. This used to query the real DB for a
vendor that *happened* to have zero PlanAllocation rows ever, but after
months of live Generate Plan/Regenerate usage every one of the 83 real
vendors now has at least one, so that query permanently returns nothing.
Everything else these tests touch (real vendors picked directly by balance,
MonthlyLedger data, etc.) is still real, unmodified data.
"""
import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import func

from backend.db.models import MonthlyLedger, Payment, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL
from backend.shared.enums import PaymentStatus
from backend.shared.payment_logging import _this_cycle_allocation, finalize_plan, log_payment, week_actual_paid
from backend.weekly_planning.calendar_utils import week_for_date

# Far enough in the future that it's always "the latest" plan_run for its
# model_used, regardless of how much real plan-generation history has
# already piled up in the real dummy DB from live testing.
_FUTURE = datetime(2099, 1, 1)


def _unallocated_vendor(session, min_balance=0):
    """Constructs a fresh, synthetic Vendor row for this test — guaranteed
    zero PlanAllocation/Payment history by construction, not by luck (see
    module docstring for why this replaced a real-DB query). Rolled back by
    the caller's own `finally: session.rollback()`, exactly like the
    PlanRun/PlanAllocation rows these tests already construct and roll back.
    A unique erp_code (uuid suffix) means concurrent/parallel test runs
    can't collide on it.
    """
    vendor = Vendor(
        erp_code=f"TEST-{uuid.uuid4().hex[:10]}",
        entity="Test Entity",
        vendor_name="Synthetic Test Vendor",
        opening_balance=float(min_balance) + 5_000_000.0,  # comfortably above min_balance and every amount these tests use
    )
    session.add(vendor)
    session.flush()  # assign vendor.id so callers can use it immediately
    return vendor


def _give_vendor_a_new_model_2_cycle_allocation(session, vendor, amount, override_amount=None, created_at=_FUTURE):
    """Constructs a plan_run/plan_allocation for a vendor, so "this
    cycle's plan allocation" is a known, controlled number — model_used
    must be NEW_MODEL_2_PLAN_RUN_LABEL (the sole model's own family) for
    _this_cycle_allocation to pick it up at all; any other model_used is
    invisible to it by design. created_at defaults far in the future so
    this is always "the latest" plan_run this cycle, regardless of
    what real testing has already generated.

    override_amount, when given, sets vendor.override_amount — the sticky,
    per-vendor, whole-month source of truth (docs/06 fix) — AND stamps the
    same value onto this row's own override_amount snapshot, mirroring what
    the real override-save endpoint does (backend/api/routers/plan_allocations.py).
    """
    if override_amount is not None:
        vendor.override_amount = override_amount
    cycle_month = session.query(func.max(MonthlyLedger.month)).scalar()
    plan_run = PlanRun(created_at=created_at, month=cycle_month, model_used=NEW_MODEL_2_PLAN_RUN_LABEL, funds_figure=amount)
    session.add(plan_run)
    session.flush()
    allocation = PlanAllocation(
        plan_run_id=plan_run.id,
        vendor_id=vendor.id,
        assigned_week=1,
        within_week_order=1,
        allocated_amount=amount,
        override_amount=override_amount,
    )
    session.add(allocation)
    session.flush()
    return allocation


def test_large_vendor_reaches_paid_in_full_against_cycle_allocation_not_total_balance():
    """docs/06's confirmed fix: a vendor with a big outstanding balance can
    still be PAID_IN_FULL for a cycle once they've received what THIS
    cycle's plan allocated them — a constructed cycle allocation far
    smaller than their real balance makes the point clearly.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=3_000_000)
        outstanding = float(vendor.opening_balance)

        cycle_allocation = 200_000.0  # constructed: deliberately small vs. the real balance
        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, cycle_allocation)

        status = log_payment(vendor.id, cycle_allocation, date(2026, 5, 10), session=session)

        assert status == PaymentStatus.PAID_IN_FULL
        # The whole point: still owes the vast majority of their real balance.
        assert float(vendor.paid_so_far_this_month) < outstanding * 0.1
    finally:
        session.rollback()
        session.close()


def test_status_transitions_not_paid_to_partial_to_full_against_cycle_allocation():
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        cycle_allocation = 1_000_000.0
        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, cycle_allocation)
        assert vendor.payment_status == PaymentStatus.NOT_PAID

        status = log_payment(vendor.id, cycle_allocation * 0.3, date(2026, 6, 1), session=session)
        assert status == PaymentStatus.PARTIAL
        assert float(vendor.paid_so_far_this_month) == pytest.approx(cycle_allocation * 0.3)

        status = log_payment(vendor.id, cycle_allocation * 0.7, date(2026, 6, 5), session=session)
        assert status == PaymentStatus.PAID_IN_FULL
        assert float(vendor.paid_so_far_this_month) == pytest.approx(cycle_allocation, abs=1)
    finally:
        session.rollback()
        session.close()


def test_overpayment_rejected_against_real_outstanding_balance():
    """The overpayment guard is unchanged by docs/06's fix — it's a hard
    ledger-integrity cap against the vendor's real balance, not the cycle
    allocation (see payment_logging.py's docstring)."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.opening_balance > 1000).order_by(Vendor.id).first()
        owed = float(vendor.opening_balance)
        with pytest.raises(ValueError):
            log_payment(vendor.id, owed * 1.5, date(2026, 6, 1), session=session)
        assert float(vendor.paid_so_far_this_month) == 0  # rejected payment must not partially apply
    finally:
        session.rollback()
        session.close()


def test_log_payment_inserts_payments_row():
    """payment_date/week are no longer caller-supplied — log_payment derives
    both from `today` (here, a fixed date standing in for "the real current
    date" so the test is deterministic) via week_for_date()."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.opening_balance > 1000).order_by(Vendor.id).first()
        today = date(2026, 6, 1)
        log_payment(vendor.id, 100.0, today=today, session=session)
        row = session.query(Payment).filter_by(vendor_id=vendor.id).one()
        assert float(row.amount) == 100.0
        assert row.payment_date == today
        assert row.week == week_for_date(today)
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize(
    "today,expected_week",
    [
        (date(2026, 3, 3), 1),  # early in the month
        (date(2026, 3, 15), 3),  # mid-month
        (date(2026, 3, 31), 5),  # last day of a 31-day month
        (date(2026, 2, 28), 4),  # last day of a non-leap Feb (only 4 weeks)
        (date(2026, 4, 1), 1),  # month-boundary: resets to week 1 the next day
    ],
)
def test_log_payment_derives_payment_date_and_week_from_today_not_client_input(today, expected_week):
    """Each payment is logged as of `today` (standing in for the real
    server-side current date, mocked here for a stable test) — no
    payment_date/week is ever passed in from a caller."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.opening_balance > 1000).order_by(Vendor.id).first()
        log_payment(vendor.id, 1.0, today=today, session=session)
        row = session.query(Payment).filter_by(vendor_id=vendor.id).order_by(Payment.id.desc()).first()
        assert row.payment_date == today
        assert row.week == expected_week
    finally:
        session.rollback()
        session.close()


def test_zero_cycle_allocation_vendor_stays_partial_regardless_of_payment_size():
    """docs/06's confirmed fix: a vendor with no PlanAllocation row at all
    this cycle has nothing to be "in full" against — cycle_allocation is 0,
    so status must cap at PARTIAL however large the payment (short of the
    real overpayment guard against outstanding balance).
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=2_000_000)

        assert _this_cycle_allocation(session, vendor.id) == 0.0  # no plan_run this cycle at all -> 0

        status = log_payment(vendor.id, 5.0, date(2026, 6, 1), session=session)  # smallest payment above MONEY_EPSILON
        assert status == PaymentStatus.PARTIAL

        status = log_payment(vendor.id, 1_000_000.0, date(2026, 6, 2), session=session)  # large payment, still no allocation
        assert status == PaymentStatus.PARTIAL
    finally:
        session.rollback()
        session.close()


def test_full_balance_paid_before_any_plan_run_reaches_paid_in_full_not_partial():
    """docs/06 fix, direct regression test: a vendor who clears their entire
    opening_balance before a plan_run has ever been generated a plan_run this cycle
    (cycle_allocation == 0) must reach PAID_IN_FULL — not get stuck at
    PARTIAL forever, since no future payment event exists to re-trigger a
    status recheck once nothing's left to pay.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=1_000_000)
        owed = float(vendor.opening_balance)

        assert _this_cycle_allocation(session, vendor.id) == 0.0  # no plan_run this cycle at all

        status = log_payment(vendor.id, owed, date(2026, 6, 1), session=session)
        assert status == PaymentStatus.PAID_IN_FULL
    finally:
        session.rollback()
        session.close()


def test_this_cycle_allocation_ignores_plan_run_month_uses_latest_by_recency_only():
    """New Model 2's own plan_run.month is Finance's picked planning month
    (docs/14), not the ledger-ingestion anchor every retired model used —
    it can legitimately differ from the vendor's real ledger month, so
    _this_cycle_allocation() must pick the latest plan_run for the model
    family by recency alone, never filtered/scoped by month. A plan_run
    dated in an unrelated month must still win if it's the most recent one.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=500_000)
        unrelated_month = date(2030, 1, 1)

        for month, amount, created_at in (
            (unrelated_month, 5_000_000.0, _FUTURE.replace(hour=1)),
            (unrelated_month, 200_000.0, _FUTURE.replace(hour=2)),  # created later -> wins, despite the same month
        ):
            plan_run = PlanRun(created_at=created_at, month=month, model_used=NEW_MODEL_2_PLAN_RUN_LABEL, funds_figure=amount)
            session.add(plan_run)
            session.flush()
            session.add(
                PlanAllocation(
                    plan_run_id=plan_run.id, vendor_id=vendor.id, assigned_week=1, within_week_order=1, allocated_amount=amount
                )
            )
            session.flush()

        assert _this_cycle_allocation(session, vendor.id) == pytest.approx(200_000.0)  # not 5,200,000

        status = log_payment(vendor.id, 200_000.0, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PAID_IN_FULL  # matches only the latest plan_run's 200,000, not 5,200,000
    finally:
        session.rollback()
        session.close()


def test_this_cycle_allocation_uses_latest_plan_run_not_a_cumulative_sum():
    """Confirmed fix: the real dashboard's Generate Plan/Regenerate button
    creates a brand-new, independent plan_run on every click (never an
    incremental top-up) — summing every one of them across the whole cycle
    made a vendor's "cycle allocation" grow without bound the more times a
    plan was regenerated, eventually making PAID_IN_FULL permanently
    unreachable no matter how much they were actually paid (confirmed live
    against the real dummy DB before this fix: one vendor's cycle allocation
    had inflated to over 22 crore from dozens of plan_runs). Only the most
    recent plan_run should count — a new click replaces what a vendor was
    told they'd get, not adds to it.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        cycle_month = session.query(func.max(MonthlyLedger.month)).scalar()

        # Three separate "Generate Plan" clicks this cycle, each its own
        # plan_run (same as the real dashboard) — allocation shrinks each
        # time as the funds figure entered changes. Relative ordering
        # between these three matters, not their absolute time, so offset
        # hours off the same future anchor.
        for hour, amount in enumerate([1_000_000.0, 700_000.0, 200_000.0]):
            plan_run = PlanRun(
                created_at=_FUTURE.replace(hour=hour),
                month=cycle_month,
                model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
                funds_figure=amount,
            )
            session.add(plan_run)
            session.flush()
            session.add(
                PlanAllocation(
                    plan_run_id=plan_run.id, vendor_id=vendor.id, assigned_week=1, within_week_order=1, allocated_amount=amount
                )
            )
            session.flush()

        cycle_allocation = _this_cycle_allocation(session, vendor.id)
        # Only the LATEST click's 200,000 — not 1,000,000+700,000+200,000=1,900,000.
        assert cycle_allocation == pytest.approx(200_000.0)

        status = log_payment(vendor.id, 200_000.0, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PAID_IN_FULL  # correctly reachable now
    finally:
        session.rollback()
        session.close()


def test_this_cycle_allocation_ignores_an_unrelated_plan_run_label():
    """Family-matched by a "model{n}%" prefix (plan_history.py's own
    convention, so a "model5_regeneration"-style label still counts) — but
    a plan_run under a completely unrelated label must never be consulted,
    even when it exists for the same vendor with a larger allocation."""
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        cycle_month = session.query(func.max(MonthlyLedger.month)).scalar()

        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, 300_000.0)

        # An unrelated, much larger plan_run for the same vendor this same
        # cycle — must have zero effect on the result.
        plan_run = PlanRun(created_at=_FUTURE, month=cycle_month, model_used="some_other_label", funds_figure=5_000_000.0)
        session.add(plan_run)
        session.flush()
        session.add(
            PlanAllocation(
                plan_run_id=plan_run.id, vendor_id=vendor.id, assigned_week=1, within_week_order=1, allocated_amount=5_000_000.0
            )
        )
        session.flush()

        assert _this_cycle_allocation(session, vendor.id) == pytest.approx(300_000.0)
    finally:
        session.rollback()
        session.close()


def test_this_cycle_allocation_prefers_override_over_allocated_amount():
    """Finance's override on the latest plan_run row takes priority
    over the model's own suggested allocated_amount."""
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, amount=1_000_000.0, override_amount=650_000.0)

        assert _this_cycle_allocation(session, vendor.id) == pytest.approx(650_000.0)  # override, not 1,000,000

        status = log_payment(vendor.id, 650_000.0, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PAID_IN_FULL  # matches the override, not the suggested amount
    finally:
        session.rollback()
        session.close()


def test_this_cycle_allocation_zero_when_no_plan_run_at_all():
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        assert _this_cycle_allocation(session, vendor.id) == 0.0
    finally:
        session.rollback()
        session.close()


# ---- finalize_plan() — "Finalize Plan" button's Budget snapshot -----------


def test_finalize_plan_snapshots_this_cycle_allocation_for_every_vendor():
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, 400_000.0)
        assert vendor.finalized_budget_amount is None  # never finalized yet

        finalize_plan(session)

        assert float(vendor.finalized_budget_amount) == pytest.approx(400_000.0)
        assert float(vendor.finalized_budget_amount) == _this_cycle_allocation(session, vendor.id)
    finally:
        session.rollback()
        session.close()


def test_finalize_plan_prefers_override_same_as_this_cycle_allocation():
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, 400_000.0, override_amount=650_000.0)

        finalize_plan(session)

        assert float(vendor.finalized_budget_amount) == pytest.approx(650_000.0)  # override, not 400,000
    finally:
        session.rollback()
        session.close()


def test_finalize_plan_does_not_move_again_until_called_a_second_time():
    """The daily/weekly workflow: Finance overrides, finalizes, pays some
    vendors — then comes back later and overrides again. Budget must not
    silently follow that second override until Finalize is clicked again."""
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        allocation = _give_vendor_a_new_model_2_cycle_allocation(session, vendor, 400_000.0)
        finalize_plan(session)
        assert float(vendor.finalized_budget_amount) == pytest.approx(400_000.0)

        vendor.override_amount = 900_000.0
        allocation.override_amount = 900_000.0
        session.flush()
        assert float(vendor.finalized_budget_amount) == pytest.approx(400_000.0)  # unchanged — not re-finalized

        finalize_plan(session)
        assert float(vendor.finalized_budget_amount) == pytest.approx(900_000.0)  # now reflects the latest override
    finally:
        session.rollback()
        session.close()


def test_vendor_fully_paid_against_suggestion_reaches_paid_in_full_end_to_end():
    """Reproduces the original bug end to end: a vendor fully paid against
    their suggested amount now correctly reaches PAID_IN_FULL — the exact
    V00742/V00093 symptom (Outstanding cleared but status stuck at Partial)
    traced back to unbounded/cross-model cycle_allocation, fixed by always
    using only the latest plan_run for the model in use.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=1_000_000)
        suggested = float(vendor.opening_balance) * 0.5  # well within their real balance either way
        _give_vendor_a_new_model_2_cycle_allocation(session, vendor, suggested)

        status = log_payment(vendor.id, suggested, date(2026, 5, 12), session=session)
        assert status == PaymentStatus.PAID_IN_FULL
    finally:
        session.rollback()
        session.close()


# ---- sticky, per-vendor override (docs/06 fix — supersedes the old,      --
# ---- per-plan_run carry_forward_paid_overrides(), now deleted) -----------


def test_override_survives_multiple_regenerations_with_changing_funds_and_zero_payments():
    """docs/06 fix: an override is a permanent, per-vendor, whole-month
    decision — it must survive every regeneration for the rest of the
    month, unchanged, with ZERO payments ever logged against the vendor
    (the common case: Finance often sets an override before paying
    anything), regardless of how the funds figure changes between runs.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=1_000_000)
        vendor.override_amount = 500_000.0  # Finance's sticky decision
        session.flush()

        # Three separate "regenerations" this cycle, each its own
        # plan_run with a different fresh suggestion/funds figure — no
        # payment ever logged against this vendor in between.
        for hour, amount in enumerate([1_000_000.0, 300_000.0, 800_000.0]):
            _give_vendor_a_new_model_2_cycle_allocation(session, vendor, amount, created_at=_FUTURE.replace(hour=hour))
            assert float(vendor.override_amount) == pytest.approx(500_000.0)  # untouched by any of them
            assert _this_cycle_allocation(session, vendor.id) == pytest.approx(500_000.0)  # override, not the suggestion
    finally:
        session.rollback()
        session.close()


# ---- Part 3: fresh end-to-end proof of the existing NOT_PAID/PARTIAL/
# PAID_IN_FULL pipeline, against both a suggested and an overridden amount ---


def test_status_outcomes_against_suggested_amount_end_to_end():
    """No override set -> status is measured against the model's own
    allocated_amount (docs/06's confirmed rule)."""
    session = SessionLocal()
    try:
        suggested = 1_000_000.0

        not_paid = _unallocated_vendor(session, min_balance=2_000_000)
        _give_vendor_a_new_model_2_cycle_allocation(session, not_paid, suggested)
        assert not_paid.payment_status == PaymentStatus.NOT_PAID  # nothing paid yet

        partial = _unallocated_vendor(session, min_balance=2_000_000)
        _give_vendor_a_new_model_2_cycle_allocation(session, partial, suggested)
        status = log_payment(partial.id, suggested * 0.4, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PARTIAL

        full = _unallocated_vendor(session, min_balance=2_000_000)
        _give_vendor_a_new_model_2_cycle_allocation(session, full, suggested)
        status = log_payment(full.id, suggested, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PAID_IN_FULL
    finally:
        session.rollback()
        session.close()


def test_status_outcomes_against_overridden_amount_end_to_end():
    """Override set -> status is measured against Finance's override, not
    the original suggested amount, in every direction (docs/06)."""
    session = SessionLocal()
    try:
        suggested = 1_000_000.0
        override = 650_000.0  # deliberately smaller than suggested

        partial = _unallocated_vendor(session, min_balance=2_000_000)
        _give_vendor_a_new_model_2_cycle_allocation(session, partial, suggested, override_amount=override)
        status = log_payment(partial.id, override * 0.5, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PARTIAL

        full = _unallocated_vendor(session, min_balance=2_000_000)
        _give_vendor_a_new_model_2_cycle_allocation(session, full, suggested, override_amount=override)
        status = log_payment(full.id, override, date(2026, 5, 10), session=session)
        assert status == PaymentStatus.PAID_IN_FULL
        # Proves it's the override driving PAID_IN_FULL, not the (larger)
        # suggested amount: what was actually paid is well below `suggested`.
        assert float(full.paid_so_far_this_month) == pytest.approx(override)
        assert float(full.paid_so_far_this_month) < suggested
    finally:
        session.rollback()
        session.close()


# ---- week_actual_paid() (display-only, live aggregation) -------------------


def test_week_actual_paid_groups_by_week_and_ignores_other_vendors():
    """_unallocated_vendor() gives a vendor with zero Payment rows too (a
    brand-new row, nobody could have paid them before this test), so no
    separate query is needed here."""
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        other = _unallocated_vendor(session)

        log_payment(vendor.id, 100.0, today=date(2026, 5, 3), session=session)  # week 1
        log_payment(vendor.id, 50.0, today=date(2026, 5, 3), session=session)  # week 1 again — same week accumulates
        log_payment(vendor.id, 200.0, today=date(2026, 5, 22), session=session)  # week 4
        log_payment(other.id, 999.0, today=date(2026, 5, 3), session=session)  # a different vendor — must not leak in

        paid = week_actual_paid(session, vendor.id)
        assert paid == {"1": pytest.approx(150.0), "4": pytest.approx(200.0)}
    finally:
        session.rollback()
        session.close()


def test_week_actual_paid_empty_for_a_vendor_with_no_payments():
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        assert week_actual_paid(session, vendor.id) == {}
    finally:
        session.rollback()
        session.close()
