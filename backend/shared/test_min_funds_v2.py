"""Unit tests for New Model 2's own Minimum Funds Required rule
(docs/14-new-model-2.md "Minimum Funds Required") — backend/shared/min_funds_v2.py.

One test per situation in the rule, using concrete numbers straight out of
docs/14's own worked examples, not just structural pass/fail. Pure-function
tests against fake ledger rows (same convention as
backend/shared/test_min_funds.py's own _FakeVendor/_FakeLedgerRow) plus a
couple of tests against the real ingested DB for the aggregate function and
parity with the untouched old rule (Commitment only).
"""
from dataclasses import dataclass
from datetime import date

import pytest

from backend.db.models import Vendor
from backend.db.session import SessionLocal
from backend.shared.enums import VendorCategory
from backend.shared.min_funds import required_amount
from backend.shared.min_funds_v2 import (
    calculate_minimum_funds_required_v2,
    min_funds_breakdown_v2,
    required_amount_v2,
)


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


CURRENT = date(2026, 7, 1)  # planning_month=Aug-26 -> as_of (planning_month - 1) = Jul-26


# ---- Rule 1: only current month outstanding, no older leftover -----------


def test_only_current_month_outstanding_is_the_trivial_full_amount():
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=10_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [_FakeLedgerRow(CURRENT, payable=10_000_000, payment=0, opening_balance=0)]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == pytest.approx(10_000_000)
    assert rule == "v2_only_current"


# ---- Rule 1b: no current AND no older — every tranche is newer than as_of.
# Only possible when as_of (planning month - 1) predates the vendor's/
# sheet's very first recorded month, e.g. Finance's first-ever planning
# cycle right after a fresh upload — real bug report: this used to silently
# return 0 for every vendor since `current` was None (as_of not found) and
# `older` was empty (nothing predates a sheet's first month either).


def test_no_data_before_as_of_uses_the_earliest_real_tranche_not_zero():
    """The exact real bug: planning month == the sheet's own first recorded
    month, so as_of (planning month - 1) has no ledger row at all, and
    nothing before it either. The vendor's only real bill (the sheet's
    first month) is NEWER than as_of, not "current" and not "older" — must
    not fall through to a flat 0."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=6_243_763.07, paid_so_far_this_month=0.0)
    sheet_first_month = date(2026, 7, 1)
    ledger_rows = [_FakeLedgerRow(sheet_first_month, payable=6_243_763.07, payment=0, opening_balance=0)]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert amount == pytest.approx(6_243_763.07)
    assert rule == "v2_no_history_before_as_of"


def test_no_data_before_as_of_picks_the_earliest_outstanding_tranche_when_several_exist():
    """Same shape, but the vendor's first FEW months are already fully paid
    off (FIFO) by the time as_of is evaluated — must pick whichever month is
    still the oldest with a real remaining balance, not blindly "the
    sheet's very first month" if that one's since been cleared."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=2_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 7, 1), payable=1_000_000, payment=1_000_000, opening_balance=0),  # fully paid
        _FakeLedgerRow(date(2026, 8, 1), payable=2_000_000, payment=0, opening_balance=0),  # still outstanding
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert amount == pytest.approx(2_000_000)
    assert rule == "v2_no_history_before_as_of"


def test_breakdown_v2_no_data_before_as_of_names_the_earliest_month_as_current():
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=6_243_763.07, paid_so_far_this_month=0.0)
    sheet_first_month = date(2026, 7, 1)
    ledger_rows = [_FakeLedgerRow(sheet_first_month, payable=6_243_763.07, payment=0, opening_balance=0)]

    detail = min_funds_breakdown_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert detail["total"] == pytest.approx(6_243_763.07)
    assert detail["rule"] == "v2_no_history_before_as_of"
    assert detail["current_month"] == sheet_first_month
    assert detail["current_amount"] == pytest.approx(6_243_763.07)
    assert detail["oldest_month"] is None  # this isn't rule 2/4's "oldest" concept
    assert detail["second_month"] is None


# ---- Rule 2: current bucket zero, an older month outstanding --------------


def test_current_bucket_zero_falls_back_to_oldest_month_only():
    """No bill at all this cycle (no ledger row for CURRENT), but Mar-26 is
    still outstanding -> Min Funds Required is just that oldest month."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=6_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [_FakeLedgerRow(date(2026, 3, 1), payable=6_000_000, payment=0, opening_balance=0)]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == pytest.approx(6_000_000)
    assert rule == "v2_current_zero_oldest_only"


def test_current_bucket_zero_via_an_explicit_zero_payable_row_falls_back_to_oldest_only():
    """Same rule 2, but this time there IS a ledger row for the current
    month — it just carries a zero payable (no bill this cycle), the other
    way "current bucket is zero" can arise (as opposed to no row at all).

    Note: a bill that was billed-then-fully-paid this same cycle can NOT
    produce "current zero, older still outstanding" — FIFO (backend/shared/
    aging.py) always drains the oldest outstanding tranche first, so as
    long as an older month is still outstanding, the current month's own
    tranche cannot have received any payment at all yet."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=6_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 3, 1), payable=6_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(CURRENT, payable=0, payment=0, opening_balance=0),
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == pytest.approx(6_000_000)
    assert rule == "v2_current_zero_oldest_only"


# ---- Rule 3: oldest <= 50% of current -> oldest + the next nonzero month -
# CORRECTED (this task): the second month is NOT always current — it's the
# first month after the oldest that still carries a real balance, walking
# forward and skipping zero-balance months. Only equals current when
# nothing outstanding sits in between.


def test_adjacent_case_second_month_is_current():
    """docs/14's own worked example: oldest May-26 ₹10L, current Jun-26
    ₹100L, nothing in between (they're adjacent) -> second month IS
    current -> Min Funds = 10L + 100L = 110L."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=11_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 5, 1), payable=1_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2026, 6, 1), payable=10_000_000, payment=0, opening_balance=0),
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert amount == pytest.approx(11_000_000)
    assert rule == "v2_oldest_and_current"


def test_oldest_exactly_50_percent_of_current_is_inclusive():
    """docs/14: "inclusive — exactly 50% counts as qualifying". Adjacent
    shape (oldest is directly one month before current), so second == current."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=15_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 3, 1), payable=5_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(CURRENT, payable=10_000_000, payment=0, opening_balance=0),
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == pytest.approx(15_000_000)
    assert rule == "v2_oldest_and_current"


def test_gap_case_second_month_found_by_skipping_one_run_of_zero_months():
    """docs/14's own worked example: oldest Jan-26 ₹15L, Feb/Mar-26 both
    zero, Apr-26 ₹8L, May-26 zero, current Jun-26 ₹100L -> walk skips Feb
    and Mar (zero), lands on Apr -> Min Funds = Jan + Apr = ₹23L. May and
    Jun (current) are excluded even though the running total (23L) is
    nowhere near 50L — the 50% check never re-runs against the sum."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=12_300_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 1, 1), payable=1_500_000, payment=0, opening_balance=0),  # oldest
        _FakeLedgerRow(date(2026, 2, 1), payable=0, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2026, 3, 1), payable=0, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2026, 4, 1), payable=800_000, payment=0, opening_balance=0),  # second (first nonzero)
        _FakeLedgerRow(date(2026, 5, 1), payable=0, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2026, 6, 1), payable=10_000_000, payment=0, opening_balance=0),  # current, excluded
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert amount == pytest.approx(2_300_000)  # 15L + 8L, NOT +100L
    assert rule == "v2_oldest_and_second"


def test_gap_case_second_month_is_a_real_nonzero_month_not_current():
    """Regression guard for the exact bug this task fixes: a REAL,
    genuinely-outstanding month (Apr) sits between the oldest (Mar) and
    current (Jul) — the second month picked must be Apr, not current, and
    Jul's own 10L bill must NOT be pulled in.

    Previously (the bug): this exact shape was asserted to equal
    Mar(3L) + Jul(10L) = 13L, "v2_oldest_and_current" — silently wrong,
    since Apr's own 4L is a real, unpaid, closer-to-oldest bill that should
    have been picked over jumping straight to current."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=17_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 3, 1), payable=3_000_000, payment=0, opening_balance=0),  # oldest
        _FakeLedgerRow(date(2026, 4, 1), payable=4_000_000, payment=0, opening_balance=0),  # second (real, nonzero)
        _FakeLedgerRow(CURRENT, payable=10_000_000, payment=0, opening_balance=0),  # current, excluded
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == pytest.approx(7_000_000)  # 3L + 4L, NOT +10L
    assert rule == "v2_oldest_and_second"


def test_never_looks_for_a_third_month_even_when_running_total_still_under_50_percent():
    """Explicit guarantee test (docs/14): the 50%-of-current check happens
    ONCE against the oldest month only, before the walk starts — it is
    never re-checked against the growing oldest+second sum. Oldest(5L) and
    second(5L) sum to 10L, still comfortably under 50% of current's 100L —
    but a genuinely outstanding third month (Mar, 5L) must still be
    excluded."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=11_500_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 1, 1), payable=500_000, payment=0, opening_balance=0),  # oldest
        _FakeLedgerRow(date(2026, 2, 1), payable=500_000, payment=0, opening_balance=0),  # second
        _FakeLedgerRow(date(2026, 3, 1), payable=500_000, payment=0, opening_balance=0),  # must be excluded
        _FakeLedgerRow(date(2026, 6, 1), payable=10_000_000, payment=0, opening_balance=0),  # current, excluded
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert amount == pytest.approx(1_000_000)  # 5L + 5L only, NOT +5L (Mar) and NOT +100L (current)
    assert rule == "v2_oldest_and_second"


# ---- Rule 4: oldest > 50% of current -> oldest only -----------------------


def test_oldest_above_50_percent_of_current_is_oldest_only():
    """docs/14's own worked example: oldest Mar-26 ₹60L is more than 50% of
    this month's ₹100L bill -> Min Funds Required = 60L only, current
    month's own bill is NOT added."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=16_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 3, 1), payable=6_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(CURRENT, payable=10_000_000, payment=0, opening_balance=0),
    ]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == pytest.approx(6_000_000)
    assert rule == "v2_oldest_only"


# ---- Rule 6: no outstanding balance at all --------------------------------


def test_no_outstanding_balance_is_zero():
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    ledger_rows = [_FakeLedgerRow(date(2026, 3, 1), payable=3_000_000, payment=3_000_000, opening_balance=0)]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=CURRENT)
    assert amount == 0.0
    assert rule == "v2_no_outstanding"


def test_no_ledger_rows_at_all_is_zero():
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=0.0, paid_so_far_this_month=0.0)
    amount, rule = required_amount_v2(vendor, [], as_of=CURRENT)
    assert amount == 0.0
    assert rule == "v2_no_outstanding"


# ---- Must Pay / Inactive share the exact same rule as Normal --------------


def test_must_pay_and_inactive_use_the_identical_rule_as_normal():
    """docs/14: Must Pay's old 'last month's own payable' formula is gone
    for New Model 2 — Must Pay, Normal, and Inactive (renamed 2026-07-24
    from Exit) all share this exact same rule now."""
    ledger_rows = [
        _FakeLedgerRow(date(2026, 3, 1), payable=6_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(CURRENT, payable=10_000_000, payment=0, opening_balance=0),
    ]
    normal = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=16_000_000.0, paid_so_far_this_month=0.0)
    must_pay = _FakeVendor(category=VendorCategory.MUST_PAY, opening_balance=16_000_000.0, paid_so_far_this_month=0.0)
    inactive_vendor = _FakeVendor(category=VendorCategory.INACTIVE, opening_balance=16_000_000.0, paid_so_far_this_month=0.0)

    normal_amount, normal_rule = required_amount_v2(normal, ledger_rows, as_of=CURRENT)
    mp_amount, mp_rule = required_amount_v2(must_pay, ledger_rows, as_of=CURRENT)
    inactive_amount, inactive_rule = required_amount_v2(inactive_vendor, ledger_rows, as_of=CURRENT)

    assert mp_amount == pytest.approx(normal_amount) == pytest.approx(inactive_amount)
    assert mp_rule == normal_rule == inactive_rule == "v2_oldest_only"


# ---- Commitment: unchanged formula, delegated to the old required_amount --


def test_commitment_delegates_to_the_untouched_old_formula():
    vendor = _FakeVendor(
        category=VendorCategory.COMMITMENT, opening_balance=24_000_000.0, paid_so_far_this_month=4_000_000.0,
        commitment_months=5,
    )
    old_amount, old_rule = required_amount(vendor, [], as_of=CURRENT)
    new_amount, new_rule = required_amount_v2(vendor, [], as_of=CURRENT)
    assert new_amount == pytest.approx(old_amount) == pytest.approx(4_000_000.0)  # (24M-4M)/5
    assert new_rule == old_rule == "commitment_outstanding_over_5_months"


# ---- Default as_of (no explicit planning month reference) -----------------


def test_default_as_of_falls_back_to_latest_ledger_month():
    """Same convention as backend/shared/aging.py's compute_vendor_aging() —
    lets a caller/test that doesn't care about New Model 2's own planning-
    month plumbing still get a sensible "current" reference."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=5_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [_FakeLedgerRow(date(2026, 6, 1), payable=5_000_000, payment=0, opening_balance=0)]

    amount, rule = required_amount_v2(vendor, ledger_rows, as_of=None)
    assert amount == pytest.approx(5_000_000)
    assert rule == "v2_only_current"


# ---- min_funds_breakdown_v2 — the vendor detail card's template fields ----


def test_breakdown_v2_adjacent_case_second_equals_current():
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=13_000_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 3, 1), payable=3_000_000, payment=0, opening_balance=0),
        _FakeLedgerRow(CURRENT, payable=10_000_000, payment=0, opening_balance=0),
    ]
    detail = min_funds_breakdown_v2(vendor, ledger_rows, as_of=CURRENT)
    assert detail["total"] == pytest.approx(13_000_000)
    assert detail["rule"] == "v2_oldest_and_current"
    assert detail["oldest_month"] == date(2026, 3, 1)
    assert detail["oldest_amount"] == pytest.approx(3_000_000)
    assert detail["current_month"] == CURRENT
    assert detail["current_amount"] == pytest.approx(10_000_000)
    assert detail["second_month"] == CURRENT  # adjacent -> second IS current
    assert detail["second_amount"] == pytest.approx(10_000_000)
    assert detail["opening_balance"] is None  # Commitment-only fields stay empty


def test_breakdown_v2_gap_case_second_is_not_current():
    """The vendor-detail-card template's "gap" wording depends on
    second_month != current_month being distinguishable — confirm the
    breakdown dict actually carries both, correctly, at once."""
    vendor = _FakeVendor(category=VendorCategory.NORMAL, opening_balance=12_300_000.0, paid_so_far_this_month=0.0)
    ledger_rows = [
        _FakeLedgerRow(date(2026, 1, 1), payable=1_500_000, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2026, 4, 1), payable=800_000, payment=0, opening_balance=0),
        _FakeLedgerRow(date(2026, 6, 1), payable=10_000_000, payment=0, opening_balance=0),
    ]
    detail = min_funds_breakdown_v2(vendor, ledger_rows, as_of=date(2026, 6, 1))
    assert detail["total"] == pytest.approx(2_300_000)
    assert detail["rule"] == "v2_oldest_and_second"
    assert detail["oldest_month"] == date(2026, 1, 1)
    assert detail["oldest_amount"] == pytest.approx(1_500_000)
    assert detail["second_month"] == date(2026, 4, 1)
    assert detail["second_amount"] == pytest.approx(800_000)
    assert detail["current_month"] == date(2026, 6, 1)  # still the real current bucket...
    assert detail["current_amount"] == pytest.approx(10_000_000)  # ...but NOT part of the total
    assert detail["second_month"] != detail["current_month"]


def test_breakdown_v2_commitment_fields():
    vendor = _FakeVendor(
        category=VendorCategory.COMMITMENT, opening_balance=24_000_000.0, paid_so_far_this_month=4_000_000.0,
        commitment_months=5,
    )
    detail = min_funds_breakdown_v2(vendor, [], as_of=CURRENT)
    assert detail["total"] == pytest.approx(4_000_000.0)
    assert detail["opening_balance"] == pytest.approx(24_000_000.0)
    assert detail["live_balance"] == pytest.approx(20_000_000.0)  # opening - paid_so_far, BEFORE dividing by months
    assert detail["commitment_months"] == 5
    assert detail["current_month"] is None  # Normal-only fields stay empty
    assert detail["oldest_month"] is None


# ---- Aggregate function against the real ingested DB ----------------------


def test_calculate_minimum_funds_required_v2_against_real_db():
    """Row count deliberately NOT hardcoded to a specific vendor total —
    the real DB's vendor count is environment-dependent (whichever Excel
    was last ingested; see memory/demo15_dataset_quirks.md), so this
    compares against the same has_outstanding_balance-filtered loader
    calculate_minimum_funds_required_v2 itself uses, not a magic number."""
    from backend.shared.min_funds import _load_vendors_and_ledger

    session = SessionLocal()
    try:
        vendors, _ = _load_vendors_and_ledger(session)
        expected_count = len(vendors)
    finally:
        session.close()

    result = calculate_minimum_funds_required_v2()
    assert result["total"] > 0
    assert result["total"] == pytest.approx(result["breakdown"]["required_amount"].sum())
    assert len(result["breakdown"]) == expected_count


def test_calculate_minimum_funds_required_v2_differs_from_the_old_rule_for_must_pay():
    """Confirms this is a real, different rule for Must Pay — not an
    accidental no-op — by comparing against the untouched old
    calculate_minimum_funds_required()'s Must Pay total on the same DB."""
    from backend.shared.min_funds import calculate_minimum_funds_required

    session = SessionLocal()
    try:
        old = calculate_minimum_funds_required(session=session)
        new = calculate_minimum_funds_required_v2(session=session)
        old_must_pay_total = old["breakdown"][old["breakdown"]["category"] == VendorCategory.MUST_PAY.value][
            "required_amount"
        ].sum()
        new_must_pay_total = new["breakdown"][new["breakdown"]["category"] == VendorCategory.MUST_PAY.value][
            "required_amount"
        ].sum()
        must_pay_vendors_exist = (
            session.query(Vendor).filter(Vendor.category == VendorCategory.MUST_PAY).count() > 0
        )
        if must_pay_vendors_exist:
            assert old_must_pay_total != pytest.approx(new_must_pay_total)
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    # ponytail: smallest runnable self-check, no framework/fixtures.
    test_only_current_month_outstanding_is_the_trivial_full_amount()
    test_no_data_before_as_of_uses_the_earliest_real_tranche_not_zero()
    test_no_data_before_as_of_picks_the_earliest_outstanding_tranche_when_several_exist()
    test_breakdown_v2_no_data_before_as_of_names_the_earliest_month_as_current()
    test_current_bucket_zero_falls_back_to_oldest_month_only()
    test_current_bucket_zero_via_an_explicit_zero_payable_row_falls_back_to_oldest_only()
    test_adjacent_case_second_month_is_current()
    test_oldest_exactly_50_percent_of_current_is_inclusive()
    test_gap_case_second_month_found_by_skipping_one_run_of_zero_months()
    test_gap_case_second_month_is_a_real_nonzero_month_not_current()
    test_never_looks_for_a_third_month_even_when_running_total_still_under_50_percent()
    test_oldest_above_50_percent_of_current_is_oldest_only()
    test_no_outstanding_balance_is_zero()
    test_no_ledger_rows_at_all_is_zero()
    test_must_pay_and_inactive_use_the_identical_rule_as_normal()
    test_commitment_delegates_to_the_untouched_old_formula()
    test_default_as_of_falls_back_to_latest_ledger_month()
    test_breakdown_v2_adjacent_case_second_equals_current()
    test_breakdown_v2_gap_case_second_is_not_current()
    test_breakdown_v2_commitment_fields()
    test_calculate_minimum_funds_required_v2_against_real_db()
    test_calculate_minimum_funds_required_v2_differs_from_the_old_rule_for_must_pay()
    print("all min_funds_v2 self-checks passed")
