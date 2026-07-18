"""Tranche-based FIFO aging — shared by Model 1 and (later) Model 2.

Each month's payable is a dated tranche. Payments are applied FIFO: the
oldest still-unpaid tranche is paid down first, regardless of which month
the payment was recorded in. What's left over per tranche after all
payments are applied is that tranche's still-outstanding amount, aged from
its origin month to `as_of` — as a whole-calendar-months-back offset, not a
day count (see AGING_BUCKETS below for why).
"""
from dataclasses import dataclass
from datetime import date

from backend.shared.constants import MONEY_EPSILON

# Bucket boundaries are pinned by product (docs/03-model1-weighted-scoring.md)
# — not a config-table item like Model 1's weights or Model 3's thresholds.
#
# Confirmed with Sarath: a pure calendar-months-back offset from `as_of`
# (the as_of month itself -> "0-30", 1 month back -> "31-60", 2 -> "61-90",
# 3 -> "91-120", 4+ -> "120+"), not a day count. Ledger tranches are monthly,
# not daily, and a day-based boundary left "31-60" structurally unreachable
# for every vendor in the real data: real months are 28-31 days, so "1 month
# back" always lands at day 28-31 depending on which two months are
# involved — e.g. a 30-day month immediately before a 31-day one jumps the
# day-age straight from 30 to 61, hopping clean over the entire 31-60
# window. Bucket *labels* are unchanged (Finance already recognizes them);
# only what lands in each one changes. lo/hi below are now months-back
# offsets, not day offsets.
AGING_BUCKETS = [
    ("0-30", 0, 0),
    ("31-60", 1, 1),
    ("61-90", 2, 2),
    ("91-120", 3, 3),
    ("120+", 4, None),
]


def _months_back(as_of, origin_month):
    """Whole calendar-month difference between origin_month and as_of — a
    pure month-index offset (year*12 + month), not a day count."""
    return (as_of.year - origin_month.year) * 12 + (as_of.month - origin_month.month)


def bucket_for_months_back(months_back):
    if months_back < 0:
        raise ValueError(f"negative tranche months-back: {months_back}")
    for label, lo, hi in AGING_BUCKETS:
        if months_back >= lo and (hi is None or months_back <= hi):
            return label
    raise AssertionError(f"unreachable: months_back={months_back}")  # last bucket is unbounded


def _day_range_label(months_back):
    """Same day-range convention as AGING_BUCKETS's labels, uncapped past
    "91-120" — "121-150" for 5 months back, "151-180" for 6, etc. Used by
    monthly_breakdown, which (unlike bucket_balances) doesn't lump 4+ months
    back into one "120+" catch-all."""
    if months_back == 0:
        return "0-30"
    return f"{months_back * 30 + 1}-{(months_back + 1) * 30}"


def _month_minus(as_of, months_back):
    """The calendar month `months_back` months before `as_of`, as the
    first-of-month date (same convention MonthlyLedger.month values use) —
    the inverse of _months_back(). Lets monthly_breakdown label each bucket
    with its real "Mon YY" alongside the existing day-range label, so
    Finance can see which calendar month a given aging bucket actually is."""
    total = as_of.year * 12 + (as_of.month - 1) - months_back
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, 1)


@dataclass
class _Tranche:
    month: date
    original: float  # the tranche's own billed amount, before any payment ever touched it
    remaining: float


@dataclass
class VendorAging:
    oldest_tranche_month: date | None
    oldest_bucket: str | None
    oldest_bucket_months_back: int | None  # real months-back for oldest_bucket, e.g. 7 or 12 for a "120+" vendor — not a flat cap
    bucket_balances: dict  # bucket label -> outstanding amount
    total_outstanding: float
    oldest_bucket_amount: float = 0.0  # docs/04 redefinition: just the single oldest tranche, not the whole lumped bucket
    monthly_breakdown: list = None  # oldest -> newest, one entry per calendar month incl. zero-balance ones

    def __post_init__(self):
        if self.monthly_breakdown is None:
            self.monthly_breakdown = []


def _apply_payment_fifo(tranches, payment):
    """Consumes `payment` against `tranches` oldest-first, mutating each
    tranche's `remaining` in place. Shared by the historical per-month loop
    below and by `already_paid_this_cycle`'s live top-up — same FIFO rule
    either way."""
    for t in tranches:
        if payment <= 0:
            break
        if t.remaining <= 0:
            continue
        applied = min(t.remaining, payment)
        t.remaining -= applied
        payment -= applied
    return payment  # whatever's left over, unapplied (an advance/credit)


def build_vendor_tranches(ledger_rows, already_paid_this_cycle=0.0):
    """ledger_rows: one vendor's MonthlyLedger rows, ordered oldest -> newest.

    Returns the vendor's real tranches (one per ledger row that actually
    carries a positive billed amount, oldest -> newest), each holding both
    its own original billed amount and its real remaining balance after
    every historical ledger payment AND the live already_paid_this_cycle
    top-up have been applied FIFO (oldest tranche paid down first,
    regardless of which month the payment was recorded in). Shared by
    compute_vendor_aging() (aging buckets) and backend/shared/min_funds.py's
    Min-Funds-Required carry-forward calculation (docs/04 fix) — one
    tranche-construction pass, not two.

    already_paid_this_cycle: a live, not-yet-ingested payment (e.g. today's
    manually logged payment against the current month) to simulate against
    the oldest outstanding tranche(s) for display purposes only — never
    written back to `monthly_ledger`, which stays exactly what ingestion
    produced. Defaults to 0.0, a no-op.

    # DEFAULT — confirm with Sarath: the vendor's pre-existing balance from
    # before the ledger window (ledger_rows[0].opening_balance, i.e. the
    # sheet's original Opening Balance column) has no known origin date of
    # its own. It's folded into the first month's tranche rather than
    # invented an earlier date — a conservative (youngest-possible) age
    # estimate for that portion of the balance.
    #
    # Confirmed in the real data: some vendors are occasionally paid ahead
    # of accrual (an advance). Any payment left over after clearing every
    # known tranche is carried forward as a credit and offsets the next
    # tranche(s) that arrive, rather than being treated as an error.
    """
    tranches = []
    advance_credit = 0.0  # payment received ahead of accrual — offsets the next tranche(s) that arrive
    for i, row in enumerate(ledger_rows):
        amount = float(row.payable)
        if i == 0:
            amount += float(row.opening_balance)
        if advance_credit > 0 and amount > 0:
            applied = min(advance_credit, amount)
            amount -= applied
            advance_credit -= applied
        if amount > 0:
            tranches.append(_Tranche(month=row.month, original=amount, remaining=amount))

        payment = _apply_payment_fifo(tranches, float(row.payment))
        if payment > MONEY_EPSILON:  # more than rounding noise left over: an advance payment ahead of accrual
            advance_credit += payment

    # Live, not-yet-ingested payment: same FIFO rule, applied on top of
    # whatever the historical ledger already left outstanding. Any leftover
    # beyond every known tranche has nothing further to age against here, so
    # (unlike the historical loop above) it isn't carried forward as credit.
    _apply_payment_fifo(tranches, float(already_paid_this_cycle))
    return tranches


def compute_vendor_aging(ledger_rows, as_of=None, already_paid_this_cycle=0.0):
    """ledger_rows: one vendor's MonthlyLedger rows, ordered oldest -> newest.

    Confirmed (docs/03-model1-weighted-scoring.md): aging is measured as of
    the master Excel's own latest ingested month, not today's real calendar
    date — a stale sheet shouldn't make every vendor look artificially
    overdue. `as_of` defaults to `ledger_rows[-1].month` for that reason;
    pass an explicit `as_of` to override.

    already_paid_this_cycle: see build_vendor_tranches() — forwarded as-is.
    """
    if not ledger_rows:
        return VendorAging(None, None, None, {}, 0.0)
    if as_of is None:
        as_of = ledger_rows[-1].month

    tranches = build_vendor_tranches(ledger_rows, already_paid_this_cycle=already_paid_this_cycle)

    # Raw per-month Payable/Payment, for the vendor detail card's own
    # Payable | Payment | Owed breakdown table (transparency alongside the
    # Aging bucket view above) — opening_balance folded into month 0's
    # payable, same as build_vendor_tranches()'s tranche.original, so
    # Payable and Owed stay mutually consistent for that first month.
    payable_by_month = {}
    payment_by_month = {}
    for i, row in enumerate(ledger_rows):
        payable_by_month[row.month] = float(row.payable) + (float(row.opening_balance) if i == 0 else 0.0)
        payment_by_month[row.month] = float(row.payment)

    bucket_balances = {label: 0.0 for label, _, _ in AGING_BUCKETS}
    remaining_by_months_back = {}
    total_outstanding = 0.0
    oldest_month = None
    for t in tranches:
        if t.remaining <= MONEY_EPSILON:  # rounding noise, not real outstanding balance
            continue
        months_back = _months_back(as_of, t.month)
        bucket_balances[bucket_for_months_back(months_back)] += t.remaining
        remaining_by_months_back[months_back] = t.remaining
        total_outstanding += t.remaining
        if oldest_month is None or t.month < oldest_month:
            oldest_month = t.month

    oldest_months_back = _months_back(as_of, oldest_month) if oldest_month else None
    oldest_bucket = bucket_for_months_back(oldest_months_back) if oldest_month else None

    # docs/04 redefinition: the oldest bill's own amount, isolated from
    # whatever else shares its coarse "120+" bucket — not bucket_balances'
    # lumped sum. Every calendar month is walked down to 0, including
    # months with nothing outstanding (no tranche object at all), so
    # Finance sees the full monthly shape, not just the non-zero months.
    oldest_bucket_amount = remaining_by_months_back.get(oldest_months_back, 0.0) if oldest_month else 0.0
    monthly_breakdown = (
        [
            {
                "months_back": mb,
                "month": _month_minus(as_of, mb),  # real calendar month, alongside the day-range label
                "label": _day_range_label(mb),
                "amount": remaining_by_months_back.get(mb, 0.0),  # still-owed balance (post-FIFO)
                "payable": payable_by_month.get(_month_minus(as_of, mb), 0.0),  # that month's own billed amount
                "payment": payment_by_month.get(_month_minus(as_of, mb), 0.0),  # raw ledger payment recorded that month
            }
            for mb in range(oldest_months_back, -1, -1)
        ]
        if oldest_month
        else []
    )

    return VendorAging(
        oldest_month,
        oldest_bucket,
        oldest_months_back,
        bucket_balances,
        total_outstanding,
        oldest_bucket_amount,
        monthly_breakdown,
    )
