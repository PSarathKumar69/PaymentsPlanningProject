"""Unit tests for the tranche-based FIFO aging module (pure logic, no DB)."""
from dataclasses import dataclass
from datetime import date

import pytest

from backend.shared.aging import _months_back, bucket_for_months_back, compute_vendor_aging


@dataclass
class FakeLedgerRow:
    month: date
    payable: float
    payment: float
    opening_balance: float


def test_bucket_boundaries():
    """Confirmed mapping: months-back from as_of, not a day count.
    0 -> 0-30, 1 -> 31-60, 2 -> 61-90, 3 -> 91-120, 4+ -> 120+."""
    assert bucket_for_months_back(0) == "0-30"
    assert bucket_for_months_back(1) == "31-60"
    assert bucket_for_months_back(2) == "61-90"
    assert bucket_for_months_back(3) == "91-120"
    assert bucket_for_months_back(4) == "120+"
    assert bucket_for_months_back(5) == "120+"
    assert bucket_for_months_back(9999) == "120+"


def test_bucket_negative_months_back_raises():
    with pytest.raises(ValueError):
        bucket_for_months_back(-1)


def test_months_back_is_a_pure_month_count_not_a_day_count():
    """The whole reason for this fix: real months are 28-31 days, so a
    day-based boundary made "31-60" structurally unreachable (a 30-day month
    immediately before a 31-day one jumped the day-age straight from 30 to
    61). months_back must give the same answer (1) regardless of which
    months' day-lengths are involved.
    """
    # 31-day month (Jan) immediately before a 28-day month (Feb, non-leap).
    assert _months_back(date(2025, 2, 1), date(2025, 1, 1)) == 1
    # 30-day month (Apr) immediately before a 31-day month (May).
    assert _months_back(date(2025, 5, 1), date(2025, 4, 1)) == 1
    # 29-day month (Feb, leap year) immediately before a 31-day month (Mar).
    assert _months_back(date(2024, 3, 1), date(2024, 2, 1)) == 1
    # Exactly the real pattern this fix was found from: Apr-26 (30 days)
    # then Mar-26 (31 days) — old day-math produced ages 30 and 61,
    # skipping "31-60" entirely; months-back correctly gives 1 and 2.
    assert _months_back(date(2026, 5, 1), date(2026, 4, 1)) == 1
    assert _months_back(date(2026, 5, 1), date(2026, 3, 1)) == 2
    # Year boundary.
    assert _months_back(date(2026, 1, 1), date(2025, 12, 1)) == 1
    assert _months_back(date(2026, 1, 1), date(2025, 9, 1)) == 4


def test_fifo_consumes_oldest_tranche_first():
    # Jan tranche 100 fully unpaid, Feb tranche 50 fully unpaid, Mar payment
    # of 120 should clear Jan (100) then eat 20 into Feb, leaving Feb at 30.
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0),
        FakeLedgerRow(date(2025, 2, 1), payable=50, payment=0, opening_balance=0),
        FakeLedgerRow(date(2025, 3, 1), payable=0, payment=120, opening_balance=0),
    ]
    aging = compute_vendor_aging(rows, as_of=date(2025, 3, 15))
    assert aging.total_outstanding == pytest.approx(30)
    assert aging.oldest_tranche_month == date(2025, 2, 1)


def test_fully_paid_vendor_has_no_oldest_bucket():
    rows = [FakeLedgerRow(date(2025, 1, 1), payable=100, payment=100, opening_balance=0)]
    aging = compute_vendor_aging(rows, as_of=date(2025, 6, 1))
    assert aging.oldest_bucket is None
    assert aging.total_outstanding == 0
    assert aging.oldest_bucket_amount == 0.0
    assert aging.monthly_breakdown == []


def test_oldest_bucket_amount_is_isolated_from_other_tranches_in_same_bucket():
    """docs/04 redefinition: a vendor with TWO tranches both past the 4-month
    mark (so both lumped into "120+" by bucket_balances) must have
    oldest_bucket_amount report only the single oldest tranche's own
    remaining balance — not the two summed together. Also proves
    monthly_breakdown walks every calendar month oldest-first, including
    the zero-balance months in between, rather than only the non-zero ones.
    """
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=300_000, payment=0, opening_balance=0),  # 7 months back
        FakeLedgerRow(date(2025, 2, 1), payable=0, payment=0, opening_balance=0),  # 6 months back
        FakeLedgerRow(date(2025, 3, 1), payable=200_000, payment=0, opening_balance=0),  # 5 months back
        FakeLedgerRow(date(2025, 4, 1), payable=0, payment=0, opening_balance=0),  # 4 months back
        FakeLedgerRow(date(2025, 5, 1), payable=0, payment=0, opening_balance=0),  # 3 months back
        FakeLedgerRow(date(2025, 6, 1), payable=0, payment=0, opening_balance=0),  # 2 months back
        FakeLedgerRow(date(2025, 7, 1), payable=0, payment=0, opening_balance=0),  # 1 month back
        FakeLedgerRow(date(2025, 8, 1), payable=0, payment=0, opening_balance=0),  # 0 months back (as_of)
    ]
    aging = compute_vendor_aging(rows, as_of=date(2025, 8, 1))

    assert aging.oldest_bucket == "120+"
    assert aging.oldest_bucket_months_back == 7
    assert aging.bucket_balances["120+"] == pytest.approx(500_000)  # old lumped-sum behavior, unchanged
    assert aging.oldest_bucket_amount == pytest.approx(300_000)  # new: just the 7-months-back tranche

    assert len(aging.monthly_breakdown) == 8
    assert [m["months_back"] for m in aging.monthly_breakdown] == [7, 6, 5, 4, 3, 2, 1, 0]
    by_months_back = {m["months_back"]: m["amount"] for m in aging.monthly_breakdown}
    assert by_months_back[7] == pytest.approx(300_000)
    assert by_months_back[5] == pytest.approx(200_000)
    for mb in (6, 4, 3, 2, 1, 0):
        assert by_months_back[mb] == 0.0
    assert aging.monthly_breakdown[0]["label"] == "211-240"
    assert aging.monthly_breakdown[-1]["label"] == "0-30"


def test_opening_balance_folded_into_first_tranche():
    rows = [FakeLedgerRow(date(2025, 1, 1), payable=0, payment=0, opening_balance=500)]
    aging = compute_vendor_aging(rows, as_of=date(2025, 2, 1))
    assert aging.total_outstanding == pytest.approx(500)
    assert aging.oldest_tranche_month == date(2025, 1, 1)


def test_oldest_bucket_months_back_recent_vendor():
    """A vendor 1 month back ("31-60") reports the real months-back number
    (1), not just the bucket label."""
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0),
        FakeLedgerRow(date(2025, 2, 1), payable=0, payment=0, opening_balance=0),
    ]
    aging = compute_vendor_aging(rows, as_of=date(2025, 2, 15))
    assert aging.oldest_bucket == "31-60"
    assert aging.oldest_bucket_months_back == 1


def test_oldest_bucket_months_back_not_capped_at_4_for_severely_overdue_vendor():
    """A vendor whose oldest tranche is 12 months back still buckets as
    "120+", but oldest_bucket_months_back must report the real 12 — not a
    flat 4, and not guessed from the "120+" label."""
    rows = [FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0)]
    aging = compute_vendor_aging(rows, as_of=date(2026, 1, 1))
    assert aging.oldest_bucket == "120+"
    assert aging.oldest_bucket_months_back == 12


def test_oldest_bucket_months_back_none_when_fully_paid():
    rows = [FakeLedgerRow(date(2025, 1, 1), payable=100, payment=100, opening_balance=0)]
    aging = compute_vendor_aging(rows, as_of=date(2025, 6, 1))
    assert aging.oldest_bucket_months_back is None


def test_default_as_of_uses_latest_ledger_month_not_real_today():
    # No as_of passed: must age off the ledger's own last month (Feb-25),
    # not whatever today's real calendar date happens to be. Jan is 1 month
    # back from Feb -> "31-60".
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0),
        FakeLedgerRow(date(2025, 2, 1), payable=0, payment=0, opening_balance=0),
    ]
    aging = compute_vendor_aging(rows)
    assert aging.oldest_bucket == "31-60"


def test_already_paid_this_cycle_default_is_backward_compatible():
    """Proves (not assumes) that adding already_paid_this_cycle didn't change
    behavior for every existing call site: omitting it must produce a
    byte-identical VendorAging to passing 0.0 explicitly."""
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=100, payment=40, opening_balance=0),
        FakeLedgerRow(date(2025, 2, 1), payable=50, payment=0, opening_balance=0),
    ]
    baseline = compute_vendor_aging(rows, as_of=date(2025, 2, 15))
    explicit_zero = compute_vendor_aging(rows, as_of=date(2025, 2, 15), already_paid_this_cycle=0.0)
    assert explicit_zero == baseline


def test_already_paid_this_cycle_consumes_oldest_tranche_first():
    # Jan 100 unpaid, Feb 50 unpaid, no historical payment yet. A live
    # (not-yet-ingested) payment of 120 today should clear Jan (100) then
    # eat 20 into Feb, leaving Feb at 30 — same FIFO rule as a historical payment.
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0),
        FakeLedgerRow(date(2025, 2, 1), payable=50, payment=0, opening_balance=0),
    ]
    aging = compute_vendor_aging(rows, as_of=date(2025, 2, 15), already_paid_this_cycle=120)
    assert aging.total_outstanding == pytest.approx(30)
    assert aging.oldest_tranche_month == date(2025, 2, 1)


def test_already_paid_this_cycle_fully_clears_outstanding():
    rows = [FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0)]
    aging = compute_vendor_aging(rows, as_of=date(2025, 3, 1), already_paid_this_cycle=100)
    assert aging.total_outstanding == 0
    assert aging.oldest_bucket is None


def test_already_paid_this_cycle_never_mutates_monthly_ledger_rows():
    """The live payment is simulated for display only — monthly_ledger's own
    payable/payment/opening_balance values must be untouched."""
    rows = [FakeLedgerRow(date(2025, 1, 1), payable=100, payment=0, opening_balance=0)]
    before = (rows[0].payable, rows[0].payment, rows[0].opening_balance)
    compute_vendor_aging(rows, as_of=date(2025, 2, 1), already_paid_this_cycle=100)
    assert (rows[0].payable, rows[0].payment, rows[0].opening_balance) == before


def test_advance_payment_becomes_credit_for_next_tranche():
    # Jan: paid 150 against a 100 tranche -> 50 advance credit, no error.
    # Feb: a 80 tranche arrives, the 50 credit offsets it, leaving 30 outstanding.
    rows = [
        FakeLedgerRow(date(2025, 1, 1), payable=100, payment=150, opening_balance=0),
        FakeLedgerRow(date(2025, 2, 1), payable=80, payment=0, opening_balance=0),
    ]
    aging = compute_vendor_aging(rows, as_of=date(2025, 2, 15))
    assert aging.total_outstanding == pytest.approx(30)
    assert aging.oldest_tranche_month == date(2025, 2, 1)
