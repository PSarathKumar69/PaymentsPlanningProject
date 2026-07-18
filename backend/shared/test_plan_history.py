"""Tests for backend/shared/plan_history.py — plan-run "history" and "model
family" helpers used by the GET /models/{n}/plan-runs endpoints and the
override-edit guard (backend/api/routers/plan_allocations.py).

Tests against the real ingested database (backend/db/app.db) for vendors/
ledger, but every PlanRun/PlanAllocation used here is explicitly constructed
(the real DB already has its own plan-run history from live testing, so
assertions check relative ordering/membership of OUR constructed rows, not
an exact total count) — always rolled back, never persisted to app.db.
"""
import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import func

from backend.db.models import MonthlyLedger, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.shared.plan_history import (
    build_plan_run_history_response,
    is_latest_plan_run,
    model_family_prefix,
    plan_runs_for_model,
)

# Far enough in the future that it's always "the latest" among any real
# plan_runs the dummy DB has already accumulated from live testing.
_FUTURE = datetime(2099, 1, 1)


def _unallocated_vendor(session, min_balance=0):
    """Guaranteed zero PlanAllocation history by construction — same
    convention already used by test_payment_logging.py/test_regeneration.py."""
    vendor = Vendor(
        erp_code=f"TEST-{uuid.uuid4().hex[:10]}",
        entity="Test Entity",
        vendor_name="Synthetic Test Vendor",
        opening_balance=float(min_balance) + 5_000_000.0,
    )
    session.add(vendor)
    session.flush()
    return vendor


def _cycle_month(session):
    return session.query(func.max(MonthlyLedger.month)).scalar()


def _plan_run(session, model_used, created_at, month):
    plan_run = PlanRun(created_at=created_at, month=month, model_used=model_used, funds_figure=100_000.0)
    session.add(plan_run)
    session.flush()
    return plan_run


def test_model_family_prefix_strips_the_regeneration_suffix():
    assert model_family_prefix("model1") == "model1"
    assert model_family_prefix("model2") == "model2"
    assert model_family_prefix("model2_regeneration") == "model2"
    assert model_family_prefix("model3_regeneration") == "model3"


def test_model_family_prefix_unrecognized_label_raises():
    with pytest.raises(ValueError):
        model_family_prefix("unspecified_regeneration")


def test_plan_runs_for_model_orders_oldest_first_across_both_model_used_labels():
    """"Plan 1, Plan 2, Plan 3..." numbering contract: a model's initial
    generate ("model2") and its later regenerations ("model2_regeneration")
    are ONE continuous, oldest-first sequence, not split by label."""
    session = SessionLocal()
    try:
        month = _cycle_month(session)
        p1 = _plan_run(session, "model2", _FUTURE.replace(hour=1), month)
        p2 = _plan_run(session, "model2_regeneration", _FUTURE.replace(hour=2), month)
        p3 = _plan_run(session, "model2_regeneration", _FUTURE.replace(hour=3), month)

        ids = [r.id for r in plan_runs_for_model(session, 2)]
        assert ids.index(p1.id) < ids.index(p2.id) < ids.index(p3.id)
    finally:
        session.rollback()
        session.close()


def test_plan_runs_for_model_excludes_other_models_and_other_months():
    session = SessionLocal()
    try:
        month = _cycle_month(session)
        mine = _plan_run(session, "model3", _FUTURE.replace(hour=4), month)
        wrong_model = _plan_run(session, "model2", _FUTURE.replace(hour=4), month)
        wrong_month = _plan_run(session, "model3", _FUTURE.replace(hour=4), date(2020, 1, 1))

        ids = {r.id for r in plan_runs_for_model(session, 3)}
        assert mine.id in ids
        assert wrong_model.id not in ids
        assert wrong_month.id not in ids
    finally:
        session.rollback()
        session.close()


def test_is_latest_plan_run_true_for_most_recent_in_family_false_for_older():
    session = SessionLocal()
    try:
        month = _cycle_month(session)
        older = _plan_run(session, "model2", _FUTURE.replace(hour=5), month)
        newer = _plan_run(session, "model2_regeneration", _FUTURE.replace(hour=6), month)

        assert is_latest_plan_run(session, newer) is True
        assert is_latest_plan_run(session, older) is False
    finally:
        session.rollback()
        session.close()


def test_is_latest_plan_run_ignores_other_models_latest():
    """A model 3 plan_run created after every model 2 plan_run must not make
    model 2's own latest plan_run look superseded — families are independent."""
    session = SessionLocal()
    try:
        month = _cycle_month(session)
        model2_latest = _plan_run(session, "model2", _FUTURE.replace(hour=7), month)
        _plan_run(session, "model3", _FUTURE.replace(hour=8), month)  # later, but a different family

        assert is_latest_plan_run(session, model2_latest) is True
    finally:
        session.rollback()
        session.close()


def test_build_plan_run_history_response_numbers_oldest_as_plan_1_and_exposes_vendor_distribution():
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        vendor.assigned_week = 1
        vendor.week_distribution_plan = {"1": 12345.0}
        month = _cycle_month(session)

        p1 = _plan_run(session, "model2", _FUTURE.replace(hour=9), month)
        session.add(
            PlanAllocation(
                plan_run_id=p1.id, vendor_id=vendor.id, assigned_week=1, within_week_order=1, allocated_amount=100.0
            )
        )
        p2 = _plan_run(session, "model2_regeneration", _FUTURE.replace(hour=10), month)
        session.add(
            PlanAllocation(
                plan_run_id=p2.id,
                vendor_id=vendor.id,
                assigned_week=1,
                within_week_order=1,
                allocated_amount=200.0,
                override_amount=150.0,
            )
        )
        session.flush()

        response = build_plan_run_history_response(session, 2)
        my_runs = [r for r in response["plan_runs"] if r["plan_run_id"] in (p1.id, p2.id)]
        assert len(my_runs) == 2
        assert my_runs[0]["plan_run_id"] == p1.id  # oldest first == "Plan 1"
        assert my_runs[1]["plan_run_id"] == p2.id  # "Plan 2"

        alloc1 = my_runs[0]["allocations"][0]
        assert alloc1["allocated_amount"] == pytest.approx(100.0)
        assert alloc1["override_amount"] is None
        # Distribution moved off the per-allocation row entirely (this task).
        assert "week_distribution_plan" not in alloc1

        alloc2 = my_runs[1]["allocations"][0]
        assert alloc2["allocated_amount"] == pytest.approx(200.0)
        assert alloc2["override_amount"] == pytest.approx(150.0)

        # The one place distribution IS surfaced: vendor-scoped, live/current,
        # regardless of which plan_run's allocations happen to be in view.
        assert response["vendor_week_distribution_plans"][str(vendor.id)] == {"1": 12345.0}
    finally:
        session.rollback()
        session.close()
