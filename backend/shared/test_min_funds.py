"""Tests against the real ingested database (backend/db/app.db) — no synthetic data.

Canonical coverage for backend.shared.min_funds's required_amount()/
calculate_minimum_funds_required()/min_funds_tranche_breakdown() — genuinely
shared logic used by New Model 2 (transitively, via planner.py/vendors.py)
and, historically, by the now-removed old models.
"""
from dataclasses import dataclass
from datetime import date

import pytest

from backend.db.models import Vendor
from backend.db.session import SessionLocal
from backend.shared.scoring import score_vendors
from backend.shared.enums import VendorCategory
from backend.shared.min_funds import (
    calculate_minimum_funds_required,
    min_funds_tranche_breakdown,
    required_amount,
)
from backend.shared.payment_logging import log_payment


def _ledger_rows(session, vendor):
    from backend.db.models import MonthlyLedger

    return session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()


def test_required_amount_per_category_from_shared_location():
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
        assert rule == "commitment_outstanding_over_1_months"

        n_ledger = _ledger_rows(session, normal)
        amount, rule = required_amount(normal, n_ledger)
        assert rule.startswith("normal_")
        assert amount >= 0
    finally:
        session.rollback()
        session.close()


def test_calculate_minimum_funds_required_from_shared_location():
    result = calculate_minimum_funds_required()
    assert result["total"] > 0
    assert result["total"] == result["breakdown"]["required_amount"].sum()
    assert len(result["breakdown"]) == 83


# ---- Live-payment-aware aging/required-amount/score (bug fix) -------------


def test_normal_vendor_required_amount_reflects_live_payment_immediately():
    """A partial payment against a Normal vendor's oldest bucket must
    immediately reduce their required_amount() — reading it straight after
    log_payment(), no separate recompute call beyond what log_payment()
    itself now does."""
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(
                Vendor.category == VendorCategory.NORMAL,
                Vendor.paid_so_far_this_month == 0,
                Vendor.opening_balance > 500_000,
            )
            .order_by(Vendor.id)
            .first()
        )
        assert vendor is not None
        ledger = _ledger_rows(session, vendor)
        before_amount, before_rule = required_amount(vendor, ledger)
        assert before_amount > 0

        payment = before_amount * 0.4  # stays within the oldest bucket — no bucket transition
        log_payment(vendor.id, payment, today=date(2026, 5, 10), session=session)

        after_amount, after_rule = required_amount(vendor, ledger)
        assert after_amount == pytest.approx(before_amount - payment, abs=1)
        assert after_rule == before_rule  # same oldest bucket, just a smaller balance in it
    finally:
        session.rollback()
        session.close()


def test_commitment_vendor_required_amount_is_live_balance_over_months():
    """Confirmed decision 3: a Commitment vendor's required amount is
    (opening_balance - paid_so_far_this_month) / commitment_months, not the
    original full balance / months."""
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(Vendor.paid_so_far_this_month == 0, Vendor.opening_balance > 500_000)
            .order_by(Vendor.id)
            .first()
        )
        vendor.category = VendorCategory.COMMITMENT
        vendor.commitment_months = 4
        opening = float(vendor.opening_balance)
        session.flush()

        payment = opening * 0.25
        log_payment(vendor.id, payment, today=date(2026, 5, 10), session=session)

        ledger = _ledger_rows(session, vendor)
        amount, rule = required_amount(vendor, ledger)
        assert amount == pytest.approx((opening - payment) / 4, abs=1)
        assert rule == "commitment_outstanding_over_4_months"
    finally:
        session.rollback()
        session.close()


def test_calculate_minimum_funds_required_total_shrinks_after_a_payment():
    """Concrete before/after: logging a payment against one vendor must
    shrink the total by exactly what was paid (that vendor's required
    amount drops by the payment; nobody else's changes)."""
    session = SessionLocal()
    try:
        before_total = float(calculate_minimum_funds_required(session=session)["total"])

        vendor = (
            session.query(Vendor)
            .filter(
                Vendor.category == VendorCategory.NORMAL,
                Vendor.paid_so_far_this_month == 0,
                Vendor.opening_balance > 100_000,
            )
            .order_by(Vendor.id)
            .first()
        )
        ledger = _ledger_rows(session, vendor)
        required_before, _ = required_amount(vendor, ledger)
        assert required_before > 0

        # Pay only part of the FIRST included tranche, not required_before as
        # a whole — a payment large enough to clear that tranche would slide
        # the carry-forward window forward onto a next tranche not yet
        # counted in required_before, and the total would then drop by less
        # than the payment (correctly — see
        # test_required_amount_normal_vendor_sums_leftover_plus_one_fresh_tranche).
        # This test isolates the simple, no-transition case instead.
        _, tranches = min_funds_tranche_breakdown(vendor, ledger)
        payment = tranches[0]["remaining_amount"] * 0.5
        log_payment(vendor.id, payment, today=date(2026, 5, 10), session=session)

        after_total = float(calculate_minimum_funds_required(session=session)["total"])

        assert after_total == pytest.approx(before_total - payment, abs=1)
        assert after_total < before_total
    finally:
        session.rollback()
        session.close()


def test_paying_one_vendor_shifts_other_vendors_outstanding_rank_too():
    """The percentile-rank point (decision 4's architectural gotcha):
    outstanding_score is rank(pct=True) across ALL vendors, so paying down
    the largest vendor doesn't just change their own score — it also raises
    the vendor immediately below them, who received no payment at all.
    """
    session = SessionLocal()
    try:
        candidates = (
            session.query(Vendor)
            .filter(Vendor.paid_so_far_this_month == 0, Vendor.opening_balance > 0)
            .order_by(Vendor.opening_balance.desc())
            .limit(2)
            .all()
        )
        assert len(candidates) == 2, "need 2 real vendors with zero paid_so_far_this_month for this scenario"
        target, runner_up = candidates
        assert float(target.opening_balance) > float(runner_up.opening_balance)

        before = score_vendors(session=session).set_index("vendor_id")
        before_target = before.loc[target.id, "outstanding_score"]
        before_runner_up = before.loc[runner_up.id, "outstanding_score"]
        assert before_target > before_runner_up  # target starts ranked above runner_up

        # Pay the target down to near-zero (but comfortably above the
        # zero-outstanding exclusion threshold, docs/06, so it doesn't drop
        # out of score_vendors() entirely) — nothing paid against runner_up.
        payment = float(target.opening_balance) - 100.0
        log_payment(target.id, payment, today=date(2026, 5, 10), session=session)

        after = score_vendors(session=session).set_index("vendor_id")
        after_target = after.loc[target.id, "outstanding_score"]
        after_runner_up = after.loc[runner_up.id, "outstanding_score"]

        assert after_target < before_target  # target's own rank collapsed, as expected
        assert after_runner_up > before_runner_up, (
            f"{runner_up.erp_code} was never paid, but their outstanding_score should "
            f"rise once {target.erp_code} drops out of contention near the top — "
            f"was {before_runner_up}, expected higher, got {after_runner_up}"
        )

        # And it's actually persisted for runner_up too, not just computed
        # in this one DataFrame — proving the whole-vendor-set write-back.
        assert float(runner_up.score) == pytest.approx(float(after.loc[runner_up.id, "score"]))
    finally:
        session.rollback()
        session.close()


# ---- required_amount() Normal-vendor carry-forward redefinition (docs/04 fix) --


@dataclass
class _FakeLedgerRow:
    month: date
    payable: float
    payment: float
    opening_balance: float


@dataclass
class _FakeVendor:
    category: VendorCategory
    opening_balance: float
    paid_so_far_this_month: float
    commitment_months: int | None = None


def test_required_amount_normal_vendor_sums_leftover_plus_one_fresh_tranche():
    """REVISED (docs/04 second fix): required_amount() itself is now
    carry-forward-aware for Normal vendors, so generate_plan()/regenerate()
    never leave a leftover balance stranded — a Finance-secured Min Funds
    figure must always be exactly what the plan can actually allocate.

    Worked example from the spec: Vendor A owes Mar 2CR (oldest), Apr 3CR,
    May 2CR, Jun 1CR. Finance pays 1.5CR against Mar, leaving 50L. The next
    cycle's required amount must be 50L (Mar leftover) + 3CR (Apr, the next
    fresh tranche) = 3.5CR — not just the 50L (the old oldest-tranche-only
    figure, now superseded)."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2025, 3, 1), payable=20_000_000, payment=15_000_000, opening_balance=0),  # Mar 2CR, paid 1.5CR
        _FakeLedgerRow(date(2025, 4, 1), payable=30_000_000, payment=0, opening_balance=0),  # Apr 3CR
        _FakeLedgerRow(date(2025, 5, 1), payable=20_000_000, payment=0, opening_balance=0),  # May 2CR
        _FakeLedgerRow(date(2025, 6, 1), payable=10_000_000, payment=0, opening_balance=0),  # Jun 1CR
    ]

    amount, rule = required_amount(vendor, ledger_rows)
    assert amount == pytest.approx(35_000_000)  # 50L leftover + Apr's 3CR
    assert rule == "normal_carry_forward_2_tranches"


def test_required_amount_transitions_to_the_next_tranche_when_a_payment_clears_the_window():
    """A payment big enough to fully clear every currently-included tranche
    and dig into the NEXT one slides the carry-forward window forward — the
    new frontier tranche (now itself a leftover) plus a fresh tranche after
    it get counted, which weren't part of required_before. So required_amount
    does NOT always shrink by exactly the payment — it can drop by less (or
    even come out higher than a naive "before - payment" prediction), because
    a new tranche has entered the window. This is intended (Option A: always
    recomputed fresh from the live ledger), not a bug."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2025, 3, 1), payable=20_000_000, payment=15_000_000, opening_balance=0),  # leftover, 50L left
        _FakeLedgerRow(date(2025, 4, 1), payable=30_000_000, payment=0, opening_balance=0),  # fresh, counted in required_before
        _FakeLedgerRow(date(2025, 5, 1), payable=20_000_000, payment=0, opening_balance=0),  # fresh, NOT yet counted
    ]

    required_before, rule_before = required_amount(vendor, ledger_rows)
    assert required_before == pytest.approx(35_000_000)  # 50L leftover + Apr's 3CR
    assert rule_before == "normal_carry_forward_2_tranches"

    # Pay enough to fully clear the leftover (50L) and dig 1CR into Apr's 3CR tranche.
    vendor.paid_so_far_this_month = 15_000_000  # 1.5CR total paid this cycle so far, on top of Mar's already-paid 1.5CR
    required_after, rule_after = required_amount(vendor, ledger_rows)

    # Apr's tranche is now the new leftover (2CR remaining of 3CR) and May's
    # 2CR fresh tranche enters the window -> 2CR + 2CR = 4CR, not 35M - 15M = 20L.
    assert required_after == pytest.approx(40_000_000)
    assert rule_after == "normal_carry_forward_2_tranches"
    assert required_after != pytest.approx(required_before - 15_000_000)  # the naive linear assumption does NOT hold here


def test_required_amount_normal_vendor_no_leftover_is_just_the_single_fresh_tranche():
    """No prior shortfall -> just the single oldest (fresh) tranche, same as
    before this fix — the carry-forward walk is a no-op when nothing's partial."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2025, 3, 1), payable=20_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2025, 4, 1), payable=30_000_000, payment=0, opening_balance=0),
    ]

    amount, rule = required_amount(vendor, ledger_rows)
    assert amount == pytest.approx(20_000_000)
    assert rule == "normal_carry_forward_1_tranche"


def test_min_funds_tranche_breakdown_flags_leftover_vs_fresh_per_line():
    """The vendor detail card's per-tranche table: one line per tranche
    (never collapsed), oldest to newest, leftover tranches flagged distinctly
    from the one fresh tranche after them."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2025, 3, 1), payable=20_000_000, payment=15_000_000, opening_balance=0),
        _FakeLedgerRow(date(2025, 4, 1), payable=30_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2025, 5, 1), payable=20_000_000, payment=0, opening_balance=0),
    ]

    total, tranches = min_funds_tranche_breakdown(vendor, ledger_rows)
    assert total == pytest.approx(35_000_000)
    assert [t["month"] for t in tranches] == [date(2025, 3, 1), date(2025, 4, 1)]
    assert tranches[0]["is_leftover"] is True
    assert tranches[0]["remaining_amount"] == pytest.approx(5_000_000)
    assert tranches[1]["is_leftover"] is False
    assert tranches[1]["remaining_amount"] == pytest.approx(30_000_000)


def test_min_funds_tranche_breakdown_has_no_tranche_list_but_a_real_total_for_must_pay_and_commitment():
    """Must Pay/Commitment have no tranche concept (tranches always []), but
    the planning table's "Total Min Funds" column must still show their real
    required_amount() — never a hardcoded 0.0."""
    ledger_rows = [_FakeLedgerRow(date(2025, 6, 1), payable=1_000_000, payment=0, opening_balance=0)]

    must_pay = _FakeVendor(category=VendorCategory.MUST_PAY, opening_balance=0.0, paid_so_far_this_month=0.0)
    total, tranches = min_funds_tranche_breakdown(must_pay, ledger_rows)
    assert total == pytest.approx(1_000_000)  # last month's payable
    assert tranches == []

    commitment = _FakeVendor(
        category=VendorCategory.COMMITMENT, opening_balance=1_000_000.0, paid_so_far_this_month=0.0, commitment_months=2
    )
    total, tranches = min_funds_tranche_breakdown(commitment, ledger_rows)
    assert total == pytest.approx(500_000)  # opening_balance / commitment_months
    assert tranches == []


def test_inactive_vendor_required_amount_matches_normal_carry_forward():
    """docs/14 Inactive design (renamed 2026-07-24 from Exit): an
    Inactive-category vendor is treated exactly like Normal for
    required_amount() — same carry-forward walk, not the Must
    Pay/Commitment formulas. Identical ledger, only category differs."""
    ledger_rows = [
        _FakeLedgerRow(date(2025, 3, 1), payable=20_000_000, payment=15_000_000, opening_balance=0),
        _FakeLedgerRow(date(2025, 4, 1), payable=30_000_000, payment=0, opening_balance=0),
    ]
    normal = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    inactive_vendor = _FakeVendor(category=VendorCategory.INACTIVE, opening_balance=0.0, paid_so_far_this_month=0.0)

    normal_amount, normal_rule = required_amount(normal, ledger_rows)
    inactive_amount, inactive_rule = required_amount(inactive_vendor, ledger_rows)
    assert inactive_amount == pytest.approx(normal_amount)
    assert inactive_rule == normal_rule
    assert inactive_rule.startswith("normal_carry_forward")  # not a must_pay_/commitment_ rule


def test_inactive_vendor_tranche_breakdown_matches_normal():
    """Same parity check as required_amount above, for the tranche-breakdown
    helper — Inactive gets the full leftover+fresh tranche table, not the
    empty-tranches Must Pay/Commitment shortcut."""
    ledger_rows = [
        _FakeLedgerRow(date(2025, 3, 1), payable=20_000_000, payment=15_000_000, opening_balance=0),
        _FakeLedgerRow(date(2025, 4, 1), payable=30_000_000, payment=0, opening_balance=0),
    ]
    normal = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    inactive_vendor = _FakeVendor(category=VendorCategory.INACTIVE, opening_balance=0.0, paid_so_far_this_month=0.0)

    normal_total, normal_tranches = min_funds_tranche_breakdown(normal, ledger_rows)
    inactive_total, inactive_tranches = min_funds_tranche_breakdown(inactive_vendor, ledger_rows)
    assert inactive_total == pytest.approx(normal_total)
    assert inactive_tranches == normal_tranches


