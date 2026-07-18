"""Tests against the real ingested database (backend/db/app.db) — no synthetic data.

Requires the ingestion step to have already run (see backend/ingestion/load_excel.py).

Category-flipping tests follow the same convention as Model 2/3's own test
suites: all 83 real vendors are currently NORMAL (no Configuration-module UI
existed yet to tag Must Pay/Commitment), so a test that needs those
categories flips a real vendor's category in-session and rolls back after.
"""
import math

import pytest

from backend.db.models import MonthlyLedger, Vendor
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import DEFAULT_WEIGHTS, generate_plan, score_vendors
from backend.shared.aging import AGING_BUCKETS
from backend.shared.enums import VendorCategory
from backend.shared.min_funds import _load_vendors_and_ledger, required_amount
from backend.weekly_planning.regeneration import regenerate

_BUCKET_ORDER = [label for label, _, _ in AGING_BUCKETS]  # freshest -> oldest


def test_weights_sum_to_one():
    assert sum(v for v, _ in DEFAULT_WEIGHTS.values()) == 1.0


def test_scores_all_83_real_vendors_with_no_missing_values():
    df = score_vendors()
    assert len(df) == 83
    for col in ["score", "aging_score", "outstanding_score", "consistency_score", "trend_score"]:
        assert df[col].notna().all(), f"{col} has a null for at least one vendor"
        assert not df[col].apply(math.isnan).any(), f"{col} has a NaN for at least one vendor"


def test_scores_are_ranked_descending():
    df = score_vendors()
    assert (df["score"].diff().dropna() <= 1e-9).all()


def test_worse_vendor_outranks_better_vendor():
    """An old-large-inconsistent-worsening vendor should outrank a
    recent-small-consistent-improving one. As of today, real aging is
    concentrated in 120+ (the historical sheet runs through May-26 only) —
    so "recent" here means this dataset's freshest available bucket, not
    a fabricated 0-30 case.
    """
    df = score_vendors()
    present_buckets = [b for b in _BUCKET_ORDER if b in df["aging_bucket"].values]
    freshest_bucket, oldest_bucket = present_buckets[0], present_buckets[-1]
    worst = df[df["aging_bucket"] == oldest_bucket].sort_values(
        ["outstanding_balance", "consistency_cv", "trend_metric"]
    ).iloc[-1]
    best = df[df["aging_bucket"] == freshest_bucket].sort_values(
        ["outstanding_balance", "consistency_cv", "trend_metric"]
    ).iloc[0]
    assert worst["score"] > best["score"]


def test_zero_outstanding_vendor_excluded_from_scoring_and_plan():
    """docs/06 fix: a vendor with nothing left to pay must never re-enter
    scoring/ranking/allocation, regardless of payment_status label."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.paid_so_far_this_month = float(vendor.opening_balance)  # zero outstanding
        session.flush()

        df = score_vendors(session=session)
        assert vendor.id not in df["vendor_id"].values

        plan = generate_plan(available_funds=1_000_000_000, session=session)
        assert vendor.id not in plan["allocations"]["vendor_id"].values
    finally:
        session.rollback()
        session.close()


# ---- generate_plan() -----------------------------------------------------


def _raw_entitlements(session):
    """Independently recomputes each Normal vendor's raw (uncapped) score/100
    x required_amount entitlement, plus the guaranteed (Must Pay/Commitment)
    total — the same inputs generate_plan() itself uses, computed here from
    scratch so the tests don't depend on generate_plan()'s own funds-level
    behavior to observe them (a huge available_funds tops everyone up to
    required_amount via the leftover rule, which would hide the raw values)."""
    vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
    scores = score_vendors(session=session).set_index("vendor_id")["score"]
    raw, required, guaranteed_total = {}, {}, 0.0
    for v in vendors:
        amount, _ = required_amount(v, ledger_by_vendor[v.id], as_of=None)
        if v.category == VendorCategory.NORMAL:
            raw[v.id] = (scores[v.id] / 100.0) * amount
            required[v.id] = amount
        else:
            guaranteed_total += amount
    return raw, required, guaranteed_total


def test_guaranteed_vendor_headroom_capped_and_relabeled_partial_on_first_generate_plan():
    """docs/06 fix: required_amount() is fixed (last month's payable for
    Must Pay) and does not shrink as the vendor gets paid down this cycle —
    a Must Pay/Commitment vendor whose real remaining headroom has fallen
    below required_amount must be capped there (ledger integrity, CLAUDE.md
    rule 1) and relabeled PARTIAL, never left GUARANTEED (rule 5's whole
    point — never let "guaranteed" silently mean "got less than promised").
    This is the very first Generate Plan of the month, no regeneration
    involved — the shared-level fix must protect this path too.
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.category = VendorCategory.MUST_PAY
        session.flush()

        ledger = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
        required, _ = required_amount(vendor, ledger)
        assert required > 0

        headroom = required * 0.5  # deliberately below required_amount
        vendor.paid_so_far_this_month = max(float(vendor.opening_balance) - headroom, 0.0)
        session.flush()

        plan = generate_plan(available_funds=1_000_000_000, session=session)  # abundant funds — not an escalation case
        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]

        assert row["status"] == "partial"
        assert row["allocated_amount"] == pytest.approx(headroom, abs=1)
        assert row["allocated_amount"] < row["required_amount"] - 1

        # escalation/guaranteed_total must stay based on the UNCAPPED
        # required_amount sum — a per-vendor headroom cap is a different
        # concept from the funds-pool-level escalation check. Recomputed
        # independently from every guaranteed-tier row's own required_amount
        # (not just this vendor's) — the real demo data already has other
        # Must Pay/Commitment vendors tagged, so guaranteed_total is their
        # combined uncapped total, not this vendor's alone.
        alloc = plan["allocations"]
        expected_guaranteed_total = alloc[alloc["category"] != "normal"]["required_amount"].sum()
        assert plan["guaranteed_total"] == pytest.approx(expected_guaranteed_total, abs=1)
        assert plan["escalation"] is False
    finally:
        session.rollback()
        session.close()


def test_must_pay_and_commitment_guaranteed_even_under_escalation():
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
        assert plan["escalation"] is True  # 1 rupee can't cover both guaranteed amounts
    finally:
        session.rollback()
        session.close()


def test_overflow_scale_down_lands_exactly_on_funds_left_and_preserves_ratios():
    session = SessionLocal()
    try:
        raw, _, guaranteed_total = _raw_entitlements(session)
        raw_total = sum(raw.values())
        assert raw_total > 0

        funds_left = raw_total / 2  # forces the overflow branch
        plan = generate_plan(available_funds=guaranteed_total + funds_left, session=session)
        scaled_normal = plan["allocations"][plan["allocations"]["status"] != "guaranteed"].set_index("vendor_id")[
            "allocated_amount"
        ]

        assert scaled_normal.sum() == pytest.approx(funds_left, rel=1e-6)

        a, b = sorted(raw, key=lambda vid: -raw[vid])[:2]  # two largest raw entitlements
        assert scaled_normal[a] / scaled_normal[b] == pytest.approx(raw[a] / raw[b], rel=1e-6)
    finally:
        session.rollback()
        session.close()


def test_leftover_after_raw_entitlements_tops_up_in_score_descending_order():
    session = SessionLocal()
    try:
        raw, required, guaranteed_total = _raw_entitlements(session)
        raw_total = sum(raw.values())
        combined_required = sum(required.values())

        # Enough for guaranteed + the raw entitlements + some genuine leftover,
        # but not enough to fully fund every Normal vendor's required_amount.
        leftover_pool = (combined_required - raw_total) * 0.3
        assert leftover_pool > 0
        funds = guaranteed_total + raw_total + leftover_pool

        plan = generate_plan(available_funds=funds, session=session)
        alloc = plan["allocations"]
        normal = alloc[alloc["status"] != "guaranteed"].sort_values("score", ascending=False)

        assert (normal["allocated_amount"] <= normal["required_amount"] + 1).all()
        assert normal["allocated_amount"].sum() == pytest.approx(raw_total + leftover_pool, abs=2)

        # Score-descending top-up order: once a vendor with a real need isn't
        # topped up to full, no lower-scoring vendor after it may be full. A
        # near-zero required_amount is excluded — within the shared ₹1 money
        # tolerance, it's trivially "full" at any allocation, which isn't a
        # real jump-the-queue and would make this check flaky.
        seen_not_full = False
        for _, row in normal[normal["required_amount"] > 1000].iterrows():
            is_full = row["allocated_amount"] >= row["required_amount"] - 1
            if seen_not_full:
                assert not is_full, "a lower-score vendor got topped up after a higher-score vendor wasn't"
            if not is_full:
                seen_not_full = True
    finally:
        session.rollback()
        session.close()


def test_no_allocation_exceeds_required_amount():
    for funds in [1, 100_000, 1_000_000_000_000]:
        plan = generate_plan(available_funds=funds)
        allocations = plan["allocations"]
        assert (allocations["allocated_amount"] <= allocations["required_amount"] + 1e-6).all()


def test_eligible_vendor_ids_filters_before_computing():
    session = SessionLocal()
    try:
        subset_ids = {v.id for v in session.query(Vendor).order_by(Vendor.id).limit(5).all()}
        plan = generate_plan(available_funds=1_000_000_000, session=session, eligible_vendor_ids=subset_ids)
        assert set(plan["allocations"]["vendor_id"]) == subset_ids
    finally:
        session.rollback()
        session.close()


def test_generate_plan_is_regenerate_compatible():
    """Proves scorer.generate_plan can be passed directly into regenerate()
    as generate_plan_fn with zero changes there — same signature as Model 2/3."""
    session = SessionLocal()
    try:
        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)
        assert result["plan_run_id"] is not None
        assert len(result["allocations"]) > 0
    finally:
        session.rollback()
        session.close()
