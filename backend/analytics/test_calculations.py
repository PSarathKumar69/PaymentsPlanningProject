"""Unit tests for backend/analytics/calculations.py — the sole home of
Analytics-tab arithmetic (task ground rule). Fresh in-memory sqlite per test
(no PAYMENTS_DB_PATH juggling needed: every function here takes `session`
explicitly and never falls back to the real backend.db.session.SessionLocal
as long as a session is passed in)."""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import Base, Config, MonthlyLedger, PlanRun, Vendor
from backend.ingestion.column_mapping_store import SHEET_START_MONTH_CONFIG_KEY
from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL
from backend.shared.enums import VendorCategory

from backend.analytics.calculations import get_analytics_dashboard, get_funds_trend


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    s.add(Config(key=SHEET_START_MONTH_CONFIG_KEY, value="2026-01-01"))
    s.commit()
    yield s
    s.close()


def _vendor(session, erp_code, name, opening_balance=0.0, paid_so_far=0.0):
    v = Vendor(
        erp_code=erp_code,
        entity="E1",
        vendor_name=name,
        opening_balance=opening_balance,
        category=VendorCategory.NORMAL,
        paid_so_far_this_month=paid_so_far,
    )
    session.add(v)
    session.flush()
    return v


def _ledger(session, vendor, month, payable, payment):
    row = MonthlyLedger(
        vendor_id=vendor.id,
        month=month,
        payable=payable,
        payment=payment,
        opening_balance=0.0,
        closing_balance=payable - payment,
    )
    session.add(row)
    return row


# ---- get_analytics_dashboard: months / aggregation -------------------------


def test_dashboard_is_empty_shape_with_no_vendors(session):
    result = get_analytics_dashboard(session=session)
    assert result["vendors"] == []
    assert result["months"] == []
    assert result["aggregates"] == []
    assert set(result["aging_totals"].values()) == {0.0}
    assert result["total_outstanding"] == 0.0
    assert set(result["outstanding_by_category"].values()) == {0.0}
    assert set(result["outstanding_by_category"]) == {"must_pay", "commitment", "normal", "inactive"}


def test_months_span_sheet_start_through_latest_ledger_month_not_a_fixed_count(session):
    """sheet_start_month is seeded to 2026-01-01 by the fixture; the latest
    ledger row here is 2026-03-01 — months must be exactly those 3, never
    hardcoded to any fixed length (CLAUDE.md rule 7)."""
    v = _vendor(session, "V1", "Vendor One", opening_balance=300.0)
    _ledger(session, v, date(2026, 1, 1), payable=100, payment=100)
    _ledger(session, v, date(2026, 2, 1), payable=100, payment=50)
    _ledger(session, v, date(2026, 3, 1), payable=100, payment=0)
    session.commit()

    result = get_analytics_dashboard(session=session)
    assert result["months"] == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert len(result["vendors"]) == 1
    assert len(result["vendors"][0]["history"]) == 3


def test_cumulative_aggregates_and_month_over_month_deltas(session):
    v1 = _vendor(session, "V1", "Vendor One", opening_balance=200.0)
    v2 = _vendor(session, "V2", "Vendor Two", opening_balance=150.0)
    _ledger(session, v1, date(2026, 1, 1), payable=100, payment=100)
    _ledger(session, v1, date(2026, 2, 1), payable=100, payment=0)
    _ledger(session, v2, date(2026, 1, 1), payable=100, payment=50)
    _ledger(session, v2, date(2026, 2, 1), payable=100, payment=0)
    session.commit()

    result = get_analytics_dashboard(session=session)
    aggregates = result["aggregates"]
    assert len(aggregates) == 2
    jan, feb = aggregates
    assert jan["total_payable"] == pytest.approx(200.0)
    assert jan["total_payment"] == pytest.approx(150.0)
    assert jan["cumulative_debt"] == pytest.approx(50.0)
    assert feb["total_payable"] == pytest.approx(200.0)
    assert feb["cumulative_debt"] == pytest.approx(250.0)  # 50 carried + 200 fresh unpaid
    debt_delta = feb["cumulative_debt"] - jan["cumulative_debt"]
    assert debt_delta == pytest.approx(200.0)


def test_overpayment_month_does_not_break_aggregation():
    """A month where payment exceeds payable (an over-payment/credit) must
    still aggregate cleanly — no exception, no NaN. Real negative-payment
    rows are already flipped to a positive over-payment by load_excel.py
    before they ever reach MonthlyLedger, so this is the shape analytics
    code actually has to tolerate."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add(Config(key=SHEET_START_MONTH_CONFIG_KEY, value="2026-01-01"))
    v = _vendor(session, "V1", "Overpaid Vendor", opening_balance=0.0)
    _ledger(session, v, date(2026, 1, 1), payable=100, payment=100)
    _ledger(session, v, date(2026, 2, 1), payable=50, payment=200)  # overpaid this month (closed, not open)
    _ledger(session, v, date(2026, 3, 1), payable=0, payment=0)  # makes Feb a CLOSED month, not the open one
    session.commit()

    result = get_analytics_dashboard(session=session)
    feb = result["aggregates"][1]
    assert feb["total_payment"] == pytest.approx(200.0)
    assert feb["cumulative_debt"] == pytest.approx(150.0 - 300.0)  # allowed to go negative, not clamped/hidden
    session.close()


def test_aging_totals_sum_every_vendors_own_buckets(session):
    v1 = _vendor(session, "V1", "Vendor One", opening_balance=100.0)
    v2 = _vendor(session, "V2", "Vendor Two", opening_balance=100.0)
    _ledger(session, v1, date(2026, 1, 1), payable=100, payment=0)
    _ledger(session, v2, date(2026, 1, 1), payable=100, payment=0)
    session.commit()

    result = get_analytics_dashboard(session=session)
    # as_of = latest ledger month = the bill's own month -> "0-30" bucket.
    assert result["aging_totals"]["0-30"] == pytest.approx(200.0)
    assert sum(result["aging_totals"].values()) == pytest.approx(200.0)


# ---- KPI-card revamp (this task): total_outstanding / outstanding_by_category --


def test_outstanding_grouped_by_category_normal_sums_p2_p3_p4_together(session):
    """Finance's explicit ask: Normal's outstanding figure is P2/P3/P4
    vendors summed as ONE bucket, not three. Vendor.category is already
    'normal' for all three priority tags (see VendorCategory docstring), so
    the group-by naturally satisfies this — this test locks that in."""
    must_pay = _vendor(session, "V1", "Must Pay Co", opening_balance=100.0)
    must_pay.category = VendorCategory.MUST_PAY
    commitment = _vendor(session, "V2", "Commitment Co", opening_balance=200.0)
    commitment.category = VendorCategory.COMMITMENT
    normal_p2 = _vendor(session, "V3", "Normal P2 Co", opening_balance=50.0)
    normal_p2.category = VendorCategory.NORMAL
    normal_p2.priority_tag = "P2"
    normal_p3 = _vendor(session, "V4", "Normal P3 Co", opening_balance=30.0)
    normal_p3.category = VendorCategory.NORMAL
    normal_p3.priority_tag = "P3"
    normal_p4 = _vendor(session, "V5", "Normal P4 Co", opening_balance=20.0)
    normal_p4.category = VendorCategory.NORMAL
    normal_p4.priority_tag = "P4"
    inactive = _vendor(session, "V6", "Inactive Co", opening_balance=400.0)
    inactive.category = VendorCategory.INACTIVE
    inactive.priority_tag = "P5"
    for v in (must_pay, commitment, normal_p2, normal_p3, normal_p4, inactive):
        _ledger(session, v, date(2026, 1, 1), payable=0, payment=0)
    session.commit()

    result = get_analytics_dashboard(session=session)
    by_cat = result["outstanding_by_category"]
    assert by_cat["must_pay"] == pytest.approx(100.0)
    assert by_cat["commitment"] == pytest.approx(200.0)
    assert by_cat["normal"] == pytest.approx(50.0 + 30.0 + 20.0)  # P2+P3+P4 summed as one
    assert by_cat["inactive"] == pytest.approx(400.0)
    assert result["total_outstanding"] == pytest.approx(100.0 + 200.0 + 100.0 + 400.0)
    assert result["total_outstanding"] == pytest.approx(sum(by_cat.values()))


# ---- Decision #2: open-month Paid substitution ------------------------------


def test_open_month_uses_paid_so_far_this_month_not_stale_ledger_payment(session):
    """The latest month in MonthlyLedger is the "open" cycle — its Paid
    figure must be the live vendor.paid_so_far_this_month (Finance has
    logged payments through the app since the last upload), not the ledger's
    own (stale) payment value. Earlier months use the ledger value as-is."""
    v = _vendor(session, "V1", "Vendor One", opening_balance=500.0, paid_so_far=999.0)
    _ledger(session, v, date(2026, 1, 1), payable=100, payment=40)  # closed month: use as recorded
    _ledger(session, v, date(2026, 2, 1), payable=200, payment=10)  # open month: ledger value is stale
    session.commit()

    result = get_analytics_dashboard(session=session)
    history = result["vendors"][0]["history"]
    jan, feb = history
    assert jan["payment"] == pytest.approx(40.0)  # closed month: untouched
    assert feb["payment"] == pytest.approx(999.0)  # open month: live paid_so_far_this_month wins

    agg_feb = result["aggregates"][1]
    assert agg_feb["total_payment"] == pytest.approx(999.0)  # aggregate uses the same substitution


# ---- get_funds_trend: only real, fully-populated PlanRun rows ---------------


def test_funds_trend_only_includes_rows_with_both_figures_populated(session):
    session.add(
        PlanRun(
            created_at=datetime(2026, 1, 5),
            month=date(2026, 1, 1),
            model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
            funds_figure=1000.0,
            min_funds_required=1200.0,
        )
    )
    # Predates this task's persistence — funds_figure only, no
    # min_funds_required yet. Must be excluded, not treated as a zero.
    session.add(
        PlanRun(
            created_at=datetime(2026, 2, 5),
            month=date(2026, 2, 1),
            model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
            funds_figure=1500.0,
            min_funds_required=None,
        )
    )
    session.commit()

    trend = get_funds_trend(session=session)
    assert len(trend) == 1
    assert trend[0] == {"month": date(2026, 1, 1), "available_funds": 1000.0, "min_funds_required": 1200.0}


def test_funds_trend_ignores_other_models_plan_runs(session):
    session.add(
        PlanRun(
            created_at=datetime(2026, 1, 5),
            month=date(2026, 1, 1),
            model_used="model1",
            funds_figure=1000.0,
            min_funds_required=1200.0,
        )
    )
    session.commit()

    assert get_funds_trend(session=session) == []


def test_funds_trend_one_point_per_month_latest_regeneration_wins(session):
    session.add(
        PlanRun(
            created_at=datetime(2026, 1, 5),
            month=date(2026, 1, 1),
            model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
            funds_figure=1000.0,
            min_funds_required=1200.0,
        )
    )
    session.add(
        PlanRun(
            created_at=datetime(2026, 1, 20),
            month=date(2026, 1, 1),
            model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
            funds_figure=1100.0,
            min_funds_required=1250.0,
        )
    )
    session.commit()

    trend = get_funds_trend(session=session)
    assert len(trend) == 1
    assert trend[0]["available_funds"] == pytest.approx(1100.0)
    assert trend[0]["min_funds_required"] == pytest.approx(1250.0)
