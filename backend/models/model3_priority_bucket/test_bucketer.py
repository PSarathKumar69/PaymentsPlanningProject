"""Tests against the real ingested database (backend/db/app.db) — no synthetic data.

All 83 real vendors are currently NORMAL (no Configuration-module UI to tag
Must Pay/Commitment yet) — tests that need those categories flip a real
vendor's category in-session and roll back afterward, same as Model 2's tests.
"""
import pytest

from backend.db.models import MonthlyLedger, Vendor
from backend.db.session import SessionLocal
from backend.models.model3_priority_bucket.bucketer import _distribute_within_bucket, assign_buckets, generate_plan
from backend.shared.enums import VendorCategory
from backend.shared.min_funds import required_amount


def test_bucket_assignment_matches_category_and_score():
    df = assign_buckets()
    assert len(df) == 83
    assert set(df["bucket"].unique()) <= {"P0", "P1", "P2", "P3", "P4"}
    normal = df[df["category"] == VendorCategory.NORMAL]
    assert (normal[normal["score"] > 70]["bucket"] == "P2").all()
    assert (normal[(normal["score"] >= 50) & (normal["score"] <= 70)]["bucket"] == "P3").all()
    assert (normal[normal["score"] < 50]["bucket"] == "P4").all()


def test_sufficient_funds_skips_percentage_caps_pays_everyone_in_full():
    session = SessionLocal()
    try:
        df = assign_buckets(session=session)
        combined_need = df[df["bucket"].isin(["P2", "P3", "P4"])]["outstanding_balance"].sum()
        plan = generate_plan(available_funds=combined_need * 2, session=session)
        normal_alloc = plan["allocations"][plan["allocations"]["bucket"].isin(["P2", "P3", "P4"])]
        assert (normal_alloc["status"] == "full").all()
        assert (normal_alloc["allocated_amount"] == normal_alloc["outstanding_balance"]).all()
    finally:
        session.rollback()
        session.close()


def test_guaranteed_vendor_headroom_capped_and_relabeled_partial_on_first_generate_plan():
    """docs/06 fix: required_amount() is fixed and does not shrink as the
    vendor gets paid down this cycle — a Must Pay/Commitment (P0/P1) vendor
    whose real remaining headroom has fallen below required_amount must be
    capped there (ledger integrity) and relabeled PARTIAL, never left
    GUARANTEED. Very first Generate Plan of the month, no regeneration.
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.category = VendorCategory.MUST_PAY
        session.flush()

        ledger = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
        required, _ = required_amount(vendor, ledger)
        assert required > 0

        headroom = required * 0.5
        vendor.paid_so_far_this_month = max(float(vendor.opening_balance) - headroom, 0.0)
        session.flush()

        plan = generate_plan(available_funds=1_000_000_000, session=session)
        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]

        assert row["status"] == "partial"
        assert row["allocated_amount"] == pytest.approx(headroom, abs=1)
        assert row["allocated_amount"] < row["required_amount"] - 1

        # Recomputed independently from every guaranteed-tier row's own
        # required_amount (not just this vendor's) — the real demo data
        # already has other Must Pay/Commitment vendors tagged. .apply()
        # (scalar, elementwise) rather than a bare Series != — bucketer's
        # own category column may hold the raw VendorCategory enum member,
        # not the plain str (see bucketer.py's own note on pandas' strict
        # type(x) is str check in vectorized comparisons).
        alloc = plan["allocations"]
        expected_guaranteed_total = alloc[alloc["category"].apply(lambda c: c != VendorCategory.NORMAL)][
            "required_amount"
        ].sum()
        assert plan["guaranteed_total"] == pytest.approx(expected_guaranteed_total, abs=1)
        assert plan["escalation"] is False
    finally:
        session.rollback()
        session.close()


def test_p0_p1_always_fully_funded_and_p4_gets_nothing_under_tight_shortfall():
    session = SessionLocal()
    try:
        vendors = session.query(Vendor).order_by(Vendor.id).limit(2).all()
        vendors[0].category = VendorCategory.MUST_PAY
        vendors[1].category = VendorCategory.COMMITMENT
        vendors[1].commitment_months = 2

        plan = generate_plan(available_funds=1, session=session)  # near-zero funds
        allocations = plan["allocations"]
        guaranteed_rows = allocations[allocations["status"] == "guaranteed"]
        assert len(guaranteed_rows) == 2
        assert (guaranteed_rows["allocated_amount"] == guaranteed_rows["required_amount"]).all()
        assert plan["escalation"] is True

        p4_rows = allocations[allocations["bucket"] == "P4"]
        assert (p4_rows["allocated_amount"] == 0).all()
        assert (p4_rows["status"] == "zero").all()
    finally:
        session.rollback()
        session.close()


def test_target_runs_out_mid_bucket_falls_back_to_score_order():
    session = SessionLocal()
    try:
        df = assign_buckets(session=session)
        p2_need = df[df["bucket"] == "P2"]["outstanding_balance"].sum()
        # Just enough for P2's 75% target and nothing else -> P3/P4 untouched,
        # and within P2 itself funds should run out mid-list (score order).
        funds = p2_need * 0.75
        plan = generate_plan(available_funds=funds, session=session)
        allocations = plan["allocations"]
        p2_rows = allocations[allocations["bucket"] == "P2"].sort_values("score", ascending=False)
        statuses = list(p2_rows["status"])
        assert "partial" in statuses or "zero" not in statuses  # some vendor is the cutoff, unless target==need exactly
        # once a vendor is "zero", every vendor after it (lower score) must also be zero
        seen_zero = False
        for s in statuses:
            if s == "zero":
                seen_zero = True
            elif seen_zero:
                assert False, "a funded vendor appears after a zero-funded one — score ordering violated"
        p3_rows = allocations[allocations["bucket"] == "P3"]
        assert (p3_rows["allocated_amount"] == 0).all()
    finally:
        session.rollback()
        session.close()


def test_leftover_after_targets_tops_up_toward_full():
    session = SessionLocal()
    try:
        df = assign_buckets(session=session)
        bucket_need = df.groupby("bucket")["outstanding_balance"].sum()
        p2_need, p3_need, p4_need = bucket_need.get("P2", 0), bucket_need.get("P3", 0), bucket_need.get("P4", 0)
        # Enough to hit all three targets (75/50/40%) with money left to spare,
        # but not enough to fully cover everyone (stays in the shortfall branch).
        funds = 0.75 * p2_need + 0.50 * p3_need + 0.40 * p4_need + 0.1 * p2_need
        assert funds < (p2_need + p3_need + p4_need)  # still a genuine shortfall
        plan = generate_plan(available_funds=funds, session=session)
        p2_alloc = plan["allocations"][plan["allocations"]["bucket"] == "P2"]["allocated_amount"].sum()
        assert p2_alloc > 0.75 * p2_need + 1  # topped up beyond the bare 75% target
    finally:
        session.rollback()
        session.close()


def test_credit_balance_vendor_gets_zero_and_does_not_inflate_funds_for_others():
    """docs/05's confirmed fix: a vendor with outstanding_balance <= 0 (a
    credit/overpayment — never explicitly addressed by docs/05) must get
    exactly 0, and must not affect funds_left at all. Placed between two
    real vendors: A funded first, credit vendor B next (previously would
    have "given back" money into funds_left), then C and D — D would have
    wrongly received funding from B's phantom refund pre-fix.
    """
    vendor_rows = [
        {"erp_code": "A", "vendor_name": "A", "score": 90, "outstanding_balance": 500_000.0},
        {"erp_code": "B", "vendor_name": "B", "score": 70, "outstanding_balance": -200_000.0},  # credit balance
        {"erp_code": "C", "vendor_name": "C", "score": 50, "outstanding_balance": 300_000.0},
        {"erp_code": "D", "vendor_name": "D", "score": 30, "outstanding_balance": 150_000.0},
    ]
    amount_available = 800_000.0  # exactly A + C's needs — nothing left for D

    allocations = {r["erp_code"]: r for r in _distribute_within_bucket(vendor_rows, amount_available)}

    assert allocations["B"]["allocated_amount"] == 0.0
    assert allocations["B"]["status"] == "zero"
    assert allocations["A"]["allocated_amount"] == pytest.approx(500_000.0)
    assert allocations["C"]["allocated_amount"] == pytest.approx(300_000.0)
    # D must get nothing — pre-fix, B's negative allocation would have
    # refunded 200,000 into funds_left, wrongly funding D from money that
    # was never in amount_available.
    assert allocations["D"]["allocated_amount"] == 0.0
    assert allocations["D"]["status"] == "zero"

    total_distributed = sum(r["allocated_amount"] for r in allocations.values())
    assert total_distributed == pytest.approx(amount_available)


def test_no_allocation_exceeds_outstanding_balance():
    session = SessionLocal()
    try:
        df = assign_buckets(session=session)
        combined_need = df["outstanding_balance"].sum()
        for funds in [1, combined_need * 0.3, combined_need * 2]:
            plan = generate_plan(available_funds=funds, session=session)
            allocations = plan["allocations"]
            assert (allocations["allocated_amount"] <= allocations["outstanding_balance"] + 1e-6).all()
    finally:
        session.rollback()
        session.close()


def test_zero_outstanding_vendor_excluded_from_plan():
    """docs/06 fix: a vendor with nothing left to pay must never re-enter
    generate_plan()'s bucket/allocation math."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.paid_so_far_this_month = float(vendor.opening_balance)  # zero outstanding
        session.flush()

        plan = generate_plan(available_funds=1_000_000_000, session=session)
        assert vendor.id not in plan["allocations"]["vendor_id"].values
    finally:
        session.rollback()
        session.close()


def test_thresholds_and_target_pcts_come_from_config():
    from backend.db.models import Config

    session = SessionLocal()
    try:
        assign_buckets(session=session)  # seeds thresholds
        generate_plan(available_funds=0, session=session)  # seeds target pcts
        keys = [
            "model3.threshold.p2_min_score",
            "model3.threshold.p3_min_score",
            "model3.target_pct.p2",
            "model3.target_pct.p3",
            "model3.target_pct.p4",
        ]
        for key in keys:
            assert session.query(Config).filter_by(key=key).first() is not None
    finally:
        session.rollback()
        session.close()
