"""Model 1 — Weighted Scoring (docs/03-model1-weighted-scoring.md).

score = 50%*aging + 0%*outstanding + 25%*consistency + 25%*trend, each
factor normalized to 0-1, final score scaled to 0-100 (Model 3's P2/P3/P4
thresholds are expressed on a 0-100 scale, e.g. "score above 70").

REVISED (confirmed with Sarath, supersedes the original 35/30/20/15 split):
outstanding amount scales with a vendor's volume/consumption, not with how
overdue or at-risk they actually are (docs/03's rationale — the AWS-vs-
freelancer example) — its weight was dropped to 0% and redistributed as a
fresh 50/25/25 split, not a proportional scaling of the old weights. Still
computed and still config-driven (not deleted from the model), so it can be
reintroduced through the Configuration module without a code change. A
vendor whose nonpayment carries outsized real consequences belongs in the
Must Pay/Commitment category (P0/P1, guaranteed before this score is even
consulted) — that's the model's mechanism for true criticality, not this
weight.

Weights are read from the `config` table (seeded with these defaults if not
already present) — never hardcoded here.
"""
import logging
import statistics
from collections import defaultdict

import pandas as pd

from backend.db.models import Config, MonthlyLedger, Vendor
from backend.db.session import SessionLocal
from backend.shared.aging import AGING_BUCKETS, compute_vendor_aging
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import AllocationStatus, VendorCategory
from backend.shared.min_funds import _load_vendors_and_ledger, has_outstanding_balance, required_amount

logger = logging.getLogger(__name__)

WEIGHT_KEYS = {
    "aging": "model1.weight.aging",
    "outstanding": "model1.weight.outstanding",
    "consistency": "model1.weight.consistency",
    "trend": "model1.weight.trend",
}
DEFAULT_WEIGHTS = {
    "aging": (0.50, "Model 1 aging factor weight"),
    "outstanding": (0.0, "Model 1 outstanding-amount factor weight — 0 by product decision (docs/03): scales with vendor volume, not urgency; still config-adjustable, not deleted from the model"),
    "consistency": (0.25, "Model 1 payment-consistency factor weight"),
    "trend": (0.25, "Model 1 payment-trend factor weight"),
}
TREND_WINDOW_MONTHS = 3  # DEFAULT — confirm with Sarath: trailing-3-month window, not pinned by product

_BUCKET_LABELS = [label for label, _, _ in AGING_BUCKETS]


def _seed_default_weights(session):
    # flush (not commit): make seeded rows visible to the query below without
    # committing a caller-supplied session's unrelated pending changes out
    # from under it. Callers that own their session commit it themselves.
    for factor, (value, description) in DEFAULT_WEIGHTS.items():
        key = WEIGHT_KEYS[factor]
        if session.query(Config).filter_by(key=key).first() is None:
            session.add(Config(key=key, value=str(value), description=description))
    session.flush()


def read_weights(session):
    _seed_default_weights(session)
    weights = {
        factor: float(session.query(Config).filter_by(key=key).one().value)
        for factor, key in WEIGHT_KEYS.items()
    }
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Model 1 weights must sum to 1.0, got {total}: {weights}")
    return weights


def _payment_ratios(ledger_rows):
    """Payment/Payable ratio per month, skipping months with zero payable (ratio undefined)."""
    return [float(r.payment) / float(r.payable) for r in ledger_rows if float(r.payable) > 0]


def _consistency_cv(ledger_rows):
    """Coefficient of variation of the monthly payment/payable ratio.

    # DEFAULT — confirm with Sarath: higher variability (less consistent
    # payment behavior relative to billing) pushes the score UP, since this
    # model's job is to surface vendors needing attention, not to reward
    # trustworthy ones with a lower score.
    """
    ratios = _payment_ratios(ledger_rows)
    if len(ratios) < 2:
        return 0.0  # not enough usable months to measure variability
    mean = statistics.mean(ratios)
    if mean == 0:
        return 0.0
    return statistics.stdev(ratios) / abs(mean)


def _trend_worsening(ledger_rows, window=TREND_WINDOW_MONTHS):
    """How much the payment/payable ratio is falling over the trailing window.

    # DEFAULT — confirm with Sarath: trailing 3 months, linear-regression
    # slope of the ratio; a falling ratio (negative slope) is "worsening"
    # and should push the score UP (more urgent). Returns -slope so bigger
    # is always more worsening/urgent.
    """
    recent = ledger_rows[-window:]
    xs, ys = [], []
    for i, row in enumerate(recent):
        payable = float(row.payable)
        if payable > 0:
            xs.append(i)
            ys.append(float(row.payment) / payable)
    if len(xs) < 2:
        return 0.0  # not enough usable months in the trailing window to fit a trend
    slope, _ = statistics.linear_regression(xs, ys)
    return -slope


def score_vendors(session=None, as_of=None):
    """Returns a pandas DataFrame, one row per vendor, sorted by score descending.

    Columns: vendor_id, erp_code, vendor_name, score, plus each factor's raw
    value and its normalized 0-1 contribution (aging_bucket/aging_score,
    outstanding_balance/outstanding_score, consistency_cv/consistency_score,
    trend_metric/trend_score) — Model 3 reuses this same breakdown.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        weights = read_weights(session)

        # docs/06 fix: a vendor with nothing left to pay must never enter
        # scoring/ranking — excluded upstream of the percentile-rank calcs
        # below, not filtered out of the result afterward.
        vendors = [v for v in session.query(Vendor).order_by(Vendor.id).all() if has_outstanding_balance(v)]
        ledger_by_vendor = defaultdict(list)
        for row in session.query(MonthlyLedger).order_by(MonthlyLedger.vendor_id, MonthlyLedger.month):
            ledger_by_vendor[row.vendor_id].append(row)

        records = []
        for vendor in vendors:
            ledger_rows = ledger_by_vendor[vendor.id]
            paid_so_far = float(vendor.paid_so_far_this_month)
            # Live-payment-aware (bug fix): a payment already logged this
            # cycle must be folded into both the aging factor and the
            # outstanding-amount factor, or a vendor paid down mid-cycle
            # keeps looking exactly as urgent as before the payment.
            aging = compute_vendor_aging(ledger_rows, as_of=as_of, already_paid_this_cycle=paid_so_far)
            aging_score = (
                _BUCKET_LABELS.index(aging.oldest_bucket) / (len(_BUCKET_LABELS) - 1)
                if aging.oldest_bucket
                else 0.0
            )
            records.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    "aging_bucket": aging.oldest_bucket,
                    "aging_score": aging_score,
                    "outstanding_balance": max(float(vendor.opening_balance) - paid_so_far, 0.0),
                    "consistency_cv": _consistency_cv(ledger_rows),
                    "trend_metric": _trend_worsening(ledger_rows),
                }
            )

        df = pd.DataFrame.from_records(records)
        # Percentile rank across vendors so no single vendor's raw magnitude
        # dominates — bigger raw value always means more urgent/attention-worthy.
        df["outstanding_score"] = df["outstanding_balance"].rank(pct=True)
        df["consistency_score"] = df["consistency_cv"].rank(pct=True)
        df["trend_score"] = df["trend_metric"].rank(pct=True)

        df["score"] = 100 * (
            weights["aging"] * df["aging_score"]
            + weights["outstanding"] * df["outstanding_score"]
            + weights["consistency"] * df["consistency_score"]
            + weights["trend"] * df["trend_score"]
        )

        result = df.sort_values("score", ascending=False).reset_index(drop=True)
        if owns_session:
            session.commit()  # persist any newly-seeded config rows
        return result
    finally:
        if owns_session:
            session.close()


def recompute_and_persist_scores(session, as_of=None):
    """Runs score_vendors() and writes every vendor's score/current_aging_bucket
    back to the DB, in the caller's own session/transaction (caller commits).

    Whole-vendor-set, never a single vendor: outstanding_score is a
    percentile rank across all vendors (df["outstanding_balance"].rank(pct=True)),
    so one vendor's payment can shift every OTHER vendor's rank too — writing
    back only the paid vendor's row would silently leave everyone else's
    score stale and wrong. Called by log_payment() so a fresh read
    immediately after logging a payment reflects live numbers.
    """
    df = score_vendors(session=session, as_of=as_of)
    vendors_by_id = {v.id: v for v in session.query(Vendor).filter(Vendor.id.in_(df["vendor_id"].tolist())).all()}
    for row in df.itertuples():
        vendor = vendors_by_id[row.vendor_id]
        vendor.score = float(row.score)
        vendor.current_aging_bucket = row.aging_bucket
    return df


def generate_plan(available_funds, session=None, as_of=None, eligible_vendor_ids=None):
    """Model 1's plan: Must Pay/Commitment guaranteed their full
    required_amount() (CLAUDE.md rule 5, same shared definition Models 2/3
    use), remaining funds split among Normal vendors by score/100 x
    required_amount, scaled down proportionally on overflow or topped up
    toward required_amount in score-descending order on leftover.

    eligible_vendor_ids: optional set of vendor ids to restrict the vendor
    set to (used by regeneration — docs/06). Defaults to None = all vendors,
    so the initial monthly plan is unaffected. Vendors outside this set are
    dropped before any funds math runs, not computed and then discarded —
    an excluded vendor must never consume part of the funds pool internally.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        if eligible_vendor_ids is not None:
            vendors = [v for v in vendors if v.id in eligible_vendor_ids]
        scores = score_vendors(session=session, as_of=as_of).set_index("vendor_id")["score"]

        rows = []
        for vendor in vendors:
            amount, rule = required_amount(vendor, ledger_by_vendor[vendor.id], as_of=as_of)
            rows.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    "category": vendor.category.value,
                    "outstanding_balance": max(float(vendor.opening_balance) - float(vendor.paid_so_far_this_month), 0.0),
                    "required_amount": amount,
                    "rule": rule,
                    "score": float(scores.get(vendor.id, 0.0)),
                }
            )

        guaranteed = [r for r in rows if r["category"] != VendorCategory.NORMAL.value]
        normal = [r for r in rows if r["category"] == VendorCategory.NORMAL.value]

        guaranteed_total = sum(r["required_amount"] for r in guaranteed)
        remaining = available_funds - guaranteed_total
        # Per CLAUDE.md rule 5, Must Pay/Commitment are always fully funded —
        # even if that pushes remaining below zero. Flag, don't shrink them.
        escalation = remaining < 0
        funds_left = max(remaining, 0.0)

        # Ledger integrity (CLAUDE.md rule 1) still applies to Must Pay/
        # Commitment vendors — required_amount() is fixed (last month's
        # payable, or outstanding/commitment_months) and does NOT shrink as
        # the vendor gets paid down this cycle, so it can end up above the
        # vendor's real remaining headroom. Cap at headroom (never below 0)
        # and relabel PARTIAL when capped — GUARANTEED must never silently
        # mean "got less than promised" (CLAUDE.md rule 5's whole point).
        # guaranteed_total above stays the uncapped required_amount sum
        # (escalation math is a separate, funds-pool-level concept — not
        # touched here), only each row's own allocated_amount changes.
        allocations = []
        for r in guaranteed:
            allocated = r["required_amount"]
            status = AllocationStatus.GUARANTEED.value
            if allocated > r["outstanding_balance"] + MONEY_EPSILON:
                logger.warning(
                    "%s: guaranteed allocation %.2f would exceed outstanding_balance %.2f — "
                    "clamping to outstanding_balance",
                    r["erp_code"],
                    allocated,
                    r["outstanding_balance"],
                )
                allocated = max(r["outstanding_balance"], 0.0)
                status = AllocationStatus.PARTIAL.value
            allocations.append({**r, "allocated_amount": allocated, "status": status})

        # Score is capped at 100, so raw_entitlement can never exceed its
        # own required_amount on its own — scaling only ever shrinks it,
        # top-up only ever raises it back up to (never past) required_amount.
        for r in normal:
            r["allocated_amount"] = (r["score"] / 100.0) * r["required_amount"]
        raw_total = sum(r["allocated_amount"] for r in normal)

        if raw_total > funds_left + MONEY_EPSILON:
            ratio = funds_left / raw_total
            for r in normal:
                r["allocated_amount"] *= ratio
        else:
            leftover = funds_left - raw_total
            for r in sorted(normal, key=lambda row: -row["score"]):
                if leftover <= 0:
                    break
                topup = min(r["required_amount"] - r["allocated_amount"], leftover)
                r["allocated_amount"] += topup
                leftover -= topup

        for r in normal:
            if r["allocated_amount"] >= r["required_amount"] - MONEY_EPSILON:
                status = AllocationStatus.FULL.value
            elif r["allocated_amount"] <= MONEY_EPSILON:
                status = AllocationStatus.ZERO.value
            else:
                status = AllocationStatus.PARTIAL.value
            allocations.append({**r, "status": status})

        return {
            "available_funds": available_funds,
            "guaranteed_total": guaranteed_total,
            "escalation": escalation,
            "escalation_shortfall": max(-remaining, 0.0),
            "allocations": pd.DataFrame(allocations),
        }
    finally:
        if owns_session:
            session.close()


if __name__ == "__main__":
    result = score_vendors()
    pd.set_option("display.width", 200)
    print(result[["erp_code", "vendor_name", "score", "aging_bucket", "outstanding_balance"]].head(10))
