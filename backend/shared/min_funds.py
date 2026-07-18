"""Minimum-funds-required calculation (docs/04-model2-min-funds-advisory.md).

Lives here, not inside Model 2's package, because Model 1 (now the sole
driver of payment_status/cycle_allocation — see payment_logging.py) needs
required_amount()/calculate_minimum_funds_required() without depending on
Model 2's package at all. Model 2 is slated for removal later; Model 3 and
the regeneration/weekly-planning modules also import from here (relocated,
not duplicated).

Two distinct, independently-triggerable actions — a hard sequencing rule,
not a preference. Both call the same required_amount() so the numbers
can't drift apart:

  - calculate_minimum_funds_required(): shown to Finance BEFORE any funds
    figure is entered or usable.
  - generate_plan(available_funds) (Model 1/2's own): runs only after
    Finance has entered that figure.
"""
from collections import defaultdict

import pandas as pd

from backend.db.models import MonthlyLedger, Vendor
from backend.db.session import SessionLocal
from backend.shared.aging import build_vendor_tranches, compute_vendor_aging
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import VendorCategory


def live_outstanding_balance(vendor):
    """The one definition of "what a vendor still actually owes right now" —
    same figure backend/api/routers/vendors.py's live_outstanding_balance and
    backend/models/model1_weighted_scoring/scorer.py's outstanding_balance
    column already compute inline. Shared here so the zero-outstanding
    exclusion below (and every other caller) can't drift from either of them.
    """
    return max(float(vendor.opening_balance) - float(vendor.paid_so_far_this_month), 0.0)


def has_outstanding_balance(vendor):
    """docs/06 fix: a vendor with nothing left to pay must never re-enter
    scoring/ranking/allocation/eligibility, regardless of what their
    payment_status label happens to say."""
    return live_outstanding_balance(vendor) > MONEY_EPSILON


def _load_vendors_and_ledger(session):
    """Shared by Model 1/2/3's generate_plan() and regeneration.py — a
    zero-outstanding vendor is excluded here, upstream of every model's
    funds math, so it's consistently absent everywhere this feeds into."""
    vendors = [v for v in session.query(Vendor).order_by(Vendor.id).all() if has_outstanding_balance(v)]
    ledger_by_vendor = defaultdict(list)
    for row in session.query(MonthlyLedger).order_by(MonthlyLedger.vendor_id, MonthlyLedger.month):
        ledger_by_vendor[row.vendor_id].append(row)
    return vendors, ledger_by_vendor


def required_amount(vendor, ledger_rows, as_of=None):
    """The one definition of a vendor's "required amount" (docs/04), by category.

    Returns (amount, rule_label). Shared by calculate_minimum_funds_required
    and every model's generate_plan() — do not recompute this separately.

    Live-payment-aware: a payment already logged this cycle
    (vendor.paid_so_far_this_month) reduces what's still required, for
    Commitment and Normal vendors alike — a payment logged mid-cycle must not
    be invisible to a later regeneration's re-scoring/re-prioritization. Must
    Pay is unaffected: its required amount is last month's payable, never a
    function of outstanding balance (confirmed simplification, unchanged).

    Normal vendors: carries forward every older tranche that was only
    partially paid in a previous cycle's shortfall, plus exactly one fresh
    (never-paid) tranche after them — not just the single oldest tranche.
    Confirmed fix: showing Finance a Min Funds Required figure that accounts
    for a leftover balance, but then having generate_plan()/regenerate() only
    ever target the oldest tranche, meant real money Finance secured based on
    that figure had nowhere to go in the actual suggested plan. One number,
    used everywhere — see _carry_forward_normal_amount for the tranche walk.
    """
    if vendor.category == VendorCategory.MUST_PAY:
        amount = float(ledger_rows[-1].payable) if ledger_rows else 0.0
        return amount, "must_pay_last_month_payable"

    if vendor.category == VendorCategory.COMMITMENT:
        months = vendor.commitment_months or 1
        live_balance = max(float(vendor.opening_balance) - float(vendor.paid_so_far_this_month), 0.0)
        return live_balance / months, f"commitment_outstanding_over_{months}_months"

    amount, included = _carry_forward_normal_amount(ledger_rows, float(vendor.paid_so_far_this_month))
    if not included:
        return 0.0, "normal_no_outstanding"
    rule = f"normal_carry_forward_{len(included)}_tranche" + ("" if len(included) == 1 else "s")
    return amount, rule


def _carry_forward_normal_amount(ledger_rows, paid_so_far):
    """docs/04 fix — Model 2's Minimum Funds Required figure for a Normal
    vendor must pick up not just the single oldest tranche
    (required_amount()'s definition, unchanged, still used by every
    model's generate_plan()/regenerate() and every other caller) but every
    older tranche that was only partially paid in a previous cycle's
    shortfall, plus exactly one fresh (never-paid) tranche after them — so
    a leftover balance doesn't silently vanish from next month's Min Funds
    figure.

    Recomputed fresh from the real ledger/payments every time (confirmed,
    Option A) — there is no stored "carry forward" field anywhere; if an
    older tranche gets paid off some other way, the next calculation just
    sees that in the real ledger, zero extra bookkeeping.

    Whether this ever actually sums MORE than one leftover tranche depends
    on whether payments are applied strictly FIFO everywhere in this
    codebase — confirmed they are: every consumer of a vendor's paid total
    (aging.py, and this module) interprets it as a single lump sum applied
    oldest-tranche-first; there is no code path anywhere that lets a
    payment target a specific, non-oldest tranche (log_payment() just
    increments one running total). Under that discipline there is at most
    one partially-paid "frontier" tranche at any moment, so this reduces
    in practice to "frontier tranche's remaining balance + one fresh
    tranche" — but the general loop below (sum every nonzero tranche up to
    and including the first fresh one) is implemented regardless, since
    it's equally correct and doesn't depend on that discipline continuing
    to hold.

    Returns (total, tranches_included) — tranches_included oldest to
    newest, each a _Tranche (month/original/remaining), for the UI's own
    per-tranche breakdown table.
    """
    tranches = build_vendor_tranches(ledger_rows, already_paid_this_cycle=paid_so_far)
    included = []
    for t in tranches:
        if t.remaining <= MONEY_EPSILON:
            continue  # already fully cleared — not part of Min Funds at all
        included.append(t)
        if t.remaining >= t.original - MONEY_EPSILON:  # a fresh tranche: no payment has ever touched it
            break  # stop right after including the first fresh one
    total = sum(t.remaining for t in included)
    return total, included


def min_funds_tranche_breakdown(vendor, ledger_rows):
    """Per-tranche breakdown for the vendor detail card's "Min Funds
    calculation" table (docs/04 fix), plus the total every vendor's "Total
    Min Funds" planning-table column shows regardless of category.

    Must Pay/Commitment have no tranche concept, so `tranches` is always []
    for them (nothing to break down line-by-line) — but `total` is still
    their real required_amount() (last month's payable / outstanding-over-
    months), never 0.0. Normal vendors get the full leftover+fresh tranche
    breakdown; `total` there equals plain Old Amt whenever there's no
    leftover to carry forward.
    """
    if vendor.category != VendorCategory.NORMAL:
        amount, _ = required_amount(vendor, ledger_rows)
        return amount, []
    total, included = _carry_forward_normal_amount(ledger_rows, float(vendor.paid_so_far_this_month))
    tranches = [
        {
            "month": t.month,
            "remaining_amount": t.remaining,
            # "Leftover" = this tranche already had some payment applied
            # before this calculation even started (a prior cycle's
            # shortfall) — the terminal tranche in `included` is always the
            # one fresh (untouched) tranche; everything before it is leftover.
            "is_leftover": t.remaining < t.original - MONEY_EPSILON,
        }
        for t in included
    ]
    return total, tranches


def _vendor_required_rows(vendors, ledger_by_vendor, as_of=None):
    """One pass computing required_amount for every vendor, with the aging
    detail Normal vendors need for generate_plan's ordering. Internal — both
    public functions below (and each model's own generate_plan()) build on
    this instead of looping twice."""
    rows = []
    for vendor in vendors:
        ledger_rows = ledger_by_vendor[vendor.id]
        amount, rule = required_amount(vendor, ledger_rows, as_of=as_of)
        paid_so_far = float(vendor.paid_so_far_this_month)
        row = {
            "vendor_id": vendor.id,
            "erp_code": vendor.erp_code,
            "vendor_name": vendor.vendor_name,
            "category": vendor.category.value,
            "commitment_months": vendor.commitment_months if vendor.category == VendorCategory.COMMITMENT else None,
            # Live figure everywhere (decision 4) — no static/live split.
            "outstanding_balance": max(float(vendor.opening_balance) - paid_so_far, 0.0),
            "required_amount": amount,
            "rule": rule,
        }
        if vendor.category == VendorCategory.NORMAL:
            aging = compute_vendor_aging(ledger_rows, as_of=as_of, already_paid_this_cycle=paid_so_far)
            row["oldest_bucket"] = aging.oldest_bucket
            row["oldest_bucket_age_days"] = (
                ((as_of or ledger_rows[-1].month) - aging.oldest_tranche_month).days
                if aging.oldest_tranche_month
                else -1
            )
        rows.append(row)
    return rows


def calculate_minimum_funds_required(session=None, as_of=None):
    """Step 2 of docs/04's flow — the number shown to Finance before any
    funds figure exists. Uses the same _vendor_required_rows()/required_amount()
    every model's generate_plan()/regenerate() also uses — one number,
    consistent everywhere, so funds Finance secures based on this figure are
    always exactly what the suggested plan can actually allocate."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        rows = _vendor_required_rows(vendors, ledger_by_vendor, as_of=as_of)
        breakdown = pd.DataFrame(rows)
        must_pay_with_balance = [
            r["erp_code"] for r in rows if r["category"] == VendorCategory.MUST_PAY.value and r["outstanding_balance"] > 1
        ]
        return {
            "total": breakdown["required_amount"].sum(),
            "breakdown": breakdown,
            "must_pay_with_outstanding_balance": must_pay_with_balance,
        }
    finally:
        if owns_session:
            session.close()
