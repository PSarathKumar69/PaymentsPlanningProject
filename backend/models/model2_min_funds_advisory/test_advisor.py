"""Tests against the real ingested database (backend/db/app.db) — no synthetic data.

Every vendor in the real DB is currently NORMAL (no UI exists yet to tag
Must Pay/Commitment) — tests that need those categories flip a real
vendor's category in-session and roll back afterward, leaving the DB as
ingestion left it.
"""
import logging

import pytest

from backend.db.models import Vendor
from backend.db.session import SessionLocal
from backend.models.model2_min_funds_advisory.advisor import (
    calculate_minimum_funds_required,
    generate_plan,
    required_amount,
)
from backend.shared import min_funds as min_funds_module
from backend.shared.enums import VendorCategory


def _ledger_rows(session, vendor):
    from backend.db.models import MonthlyLedger

    return session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()


def test_required_amount_per_category():
    session = SessionLocal()
    try:
        vendors = session.query(Vendor).order_by(Vendor.id).limit(3).all()
        must_pay, commitment, normal = vendors
        must_pay.category = VendorCategory.MUST_PAY
        commitment.category = VendorCategory.COMMITMENT
        commitment.commitment_months = 1
        normal.category = VendorCategory.NORMAL

        mp_ledger = _ledger_rows(session, must_pay)
        amount, rule = required_amount(must_pay, mp_ledger)
        assert amount == float(mp_ledger[-1].payable)
        assert rule == "must_pay_last_month_payable"

        c_ledger = _ledger_rows(session, commitment)
        amount, rule = required_amount(commitment, c_ledger)
        assert amount == float(commitment.opening_balance)  # commitment_months=1 -> full balance
        assert "commitment_outstanding_over_1_months" == rule

        n_ledger = _ledger_rows(session, normal)
        amount, rule = required_amount(normal, n_ledger)
        assert rule.startswith("normal_")
        assert amount >= 0
    finally:
        session.rollback()
        session.close()


def test_commitment_months_divides_outstanding_balance():
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter(Vendor.opening_balance > 0).first()
        vendor.category = VendorCategory.COMMITMENT
        vendor.commitment_months = 4
        ledger = _ledger_rows(session, vendor)

        amount, rule = required_amount(vendor, ledger)
        assert amount == float(vendor.opening_balance) / 4
        assert rule == "commitment_outstanding_over_4_months"
    finally:
        session.rollback()
        session.close()


def test_minimum_required_total_is_sane():
    result = calculate_minimum_funds_required()
    assert result["total"] > 0
    assert result["total"] == result["breakdown"]["required_amount"].sum()
    assert len(result["breakdown"]) == 83


def test_guaranteed_vendor_headroom_capped_and_relabeled_partial_on_first_generate_plan():
    """docs/06 fix: required_amount() is fixed and does not shrink as the
    vendor gets paid down this cycle — a Must Pay/Commitment vendor whose
    real remaining headroom has fallen below required_amount must be capped
    there (ledger integrity) and relabeled PARTIAL, never left GUARANTEED.
    Very first Generate Plan of the month, no regeneration involved.
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.category = VendorCategory.MUST_PAY
        session.flush()

        ledger = _ledger_rows(session, vendor)
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
        # already has other Must Pay/Commitment vendors tagged.
        alloc = plan["allocations"]
        expected_guaranteed_total = alloc[alloc["category"] != "normal"]["required_amount"].sum()
        assert plan["guaranteed_total"] == pytest.approx(expected_guaranteed_total, abs=1)
        assert plan["escalation"] is False
    finally:
        session.rollback()
        session.close()


def test_must_pay_and_commitment_always_fully_funded_under_severe_shortfall():
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
        for _, row in guaranteed_rows.iterrows():
            assert row["allocated_amount"] == row["required_amount"]
        assert plan["escalation"] is True  # 1 rupee can't cover both guaranteed amounts
    finally:
        session.rollback()
        session.close()


def test_no_allocation_exceeds_outstanding_balance():
    result = calculate_minimum_funds_required()
    plan = generate_plan(available_funds=result["total"] * 2)  # comfortably above minimum
    allocations = plan["allocations"]
    assert (allocations["allocated_amount"] <= allocations["outstanding_balance"] + 1e-6).all()


def test_bad_data_vendor_clamped_and_isolated_others_unaffected(monkeypatch, caplog):
    """docs/04's confirmed fix: a single vendor's inconsistent data (their
    required_amount would exceed their real outstanding_balance) must not
    abort the whole plan — that vendor gets clamped to outstanding_balance
    with a logged warning, everyone else's allocation is untouched.
    """
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 100_000)
            .order_by(Vendor.id)
            .first()
        )
        available_funds = 1_000_000_000  # comfortably above every real vendor's need

        baseline = generate_plan(available_funds=available_funds, session=session)
        baseline_others = baseline["allocations"][baseline["allocations"]["vendor_id"] != vendor.id].set_index(
            "vendor_id"
        )["allocated_amount"]

        real_required_amount = min_funds_module.required_amount

        def bad_required_amount(v, ledger_rows, as_of=None):
            if v.id == vendor.id:
                return float(v.opening_balance) + 500_000.0, "inflated_test_bug"  # bad/inconsistent data
            return real_required_amount(v, ledger_rows, as_of=as_of)

        # Patched on backend.shared.min_funds (where required_amount now
        # actually lives and where _vendor_required_rows looks it up) —
        # patching backend.models.model2_min_funds_advisory.advisor's
        # re-exported name would not affect generate_plan()'s behavior,
        # since that name resolution happens inside min_funds's own module
        # globals, not advisor.py's.
        monkeypatch.setattr(min_funds_module, "required_amount", bad_required_amount)

        with caplog.at_level(logging.WARNING):
            plan = generate_plan(available_funds=available_funds, session=session)

        allocations = plan["allocations"]
        assert len(allocations) == 83  # completed for every vendor — didn't abort

        row = allocations[allocations["vendor_id"] == vendor.id].iloc[0]
        assert row["allocated_amount"] == pytest.approx(float(vendor.opening_balance), abs=1)
        assert row["allocated_amount"] < row["required_amount"] - 1  # clamped below the bad inflated figure
        assert any(vendor.erp_code in rec.message for rec in caplog.records)  # names the vendor

        others = allocations[allocations["vendor_id"] != vendor.id].set_index("vendor_id")["allocated_amount"]
        assert (others - baseline_others).abs().max() < 1  # every other vendor unaffected
    finally:
        session.rollback()
        session.close()


def test_subrupee_noise_does_not_trigger_overpayment_clamp(monkeypatch, caplog):
    """docs/04's confirmed fix: the overpayment check now uses the shared
    ₹1 tolerance, not 1e-6 — ordinary float noise on real rupee amounts
    must not spuriously clamp (or, before Fix 2, crash) a vendor.
    """
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 100_000)
            .order_by(Vendor.id)
            .first()
        )
        real_required_amount = min_funds_module.required_amount

        def noisy_required_amount(v, ledger_rows, as_of=None):
            if v.id == vendor.id:
                return float(v.opening_balance) + 0.5, "noisy_test_amount"  # sub-rupee float noise, not a real overpayment
            return real_required_amount(v, ledger_rows, as_of=as_of)

        monkeypatch.setattr(min_funds_module, "required_amount", noisy_required_amount)

        with caplog.at_level(logging.WARNING):
            plan = generate_plan(available_funds=1_000_000_000, session=session)

        row = plan["allocations"][plan["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["status"] == "full"
        assert row["allocated_amount"] == pytest.approx(row["required_amount"], abs=1e-6)  # not clamped down
        assert not any(vendor.erp_code in rec.message for rec in caplog.records)  # no spurious warning
    finally:
        session.rollback()
        session.close()


def test_zero_outstanding_vendor_excluded_from_plan():
    """docs/06 fix: a vendor with nothing left to pay must never re-enter
    generate_plan()'s allocation math."""
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


def test_older_normal_vendor_outranks_newer_one_when_funds_tight():
    plan_tiny = generate_plan(available_funds=1)  # forces "zero" status for all but the very oldest
    normal_rows = plan_tiny["allocations"][plan_tiny["allocations"]["category"] == "normal"]
    funded = normal_rows[normal_rows["allocated_amount"] > 0]
    assert len(funded) <= 1  # at most a single partially-funded vendor at this funding level
    if len(funded):
        funded_age = funded.iloc[0]["oldest_bucket_age_days"]
        others_age = normal_rows[normal_rows["allocated_amount"] == 0]["oldest_bucket_age_days"]
        assert (funded_age >= others_age).all()
