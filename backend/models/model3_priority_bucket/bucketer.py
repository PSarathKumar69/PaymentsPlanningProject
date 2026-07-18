"""Model 3 — Priority Bucket (docs/05-model3-priority-bucket.md).

Bucket assignment: MUST_PAY -> P0, COMMITMENT -> P1 (Finance-tagged,
persistent), NORMAL -> P2/P3/P4 by Model 1's score (reused directly, not
recomputed). P0/P1 are always fully funded first, via Model 2's
required_amount() (reused directly). Whatever's left is shared by
P2/P3/P4: paid in full if funds allow, otherwise spread via a
percentage-of-own-need target per bucket, processed in priority order.
"""
import logging

import pandas as pd

from backend.db.models import Config, Vendor
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import score_vendors
from backend.models.model2_min_funds_advisory.advisor import _load_vendors_and_ledger, required_amount
from backend.shared.constants import MONEY_EPSILON as _MONEY_EPSILON
from backend.shared.enums import AllocationStatus, PriorityBucket, VendorCategory

logger = logging.getLogger(__name__)

SCORE_THRESHOLD_SPEC = {
    "model3.threshold.p2_min_score": (70, "Normal vendors scoring above this go to P2"),
    "model3.threshold.p3_min_score": (50, "Normal vendors scoring at/above this (and not P2) go to P3; below goes to P4"),
}
TARGET_PCT_SPEC = {
    "model3.target_pct.p2": (0.75, "P2 shortfall target: % of P2's own total need"),
    "model3.target_pct.p3": (0.50, "P3 shortfall target: % of P3's own total need"),
    "model3.target_pct.p4": (0.40, "P4 shortfall target: % of P4's own total need"),
}
# .value (plain str), not the enum member itself: these feed straight into
# DataFrame columns/isin() below, and pandas' string dtype does a strict
# type(x) is str check in vectorized comparisons — a str-subclass enum
# member matches fine scalar-to-scalar but silently fails Series-wide.
BUCKET_ORDER = [PriorityBucket.P2.value, PriorityBucket.P3.value, PriorityBucket.P4.value]


def _seed_and_read(session, spec):
    # flush (not commit): make seeded rows visible to the query below without
    # committing a caller-supplied session's unrelated pending changes out
    # from under it. Callers that own their session commit it themselves.
    for key, (value, description) in spec.items():
        if session.query(Config).filter_by(key=key).first() is None:
            session.add(Config(key=key, value=str(value), description=description))
    session.flush()
    return {key: float(session.query(Config).filter_by(key=key).one().value) for key in spec}


def assign_buckets(session=None, as_of=None):
    """Model 1's score_vendors() output, plus `category` and `bucket` (P0-P4) columns."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        thresholds = _seed_and_read(session, SCORE_THRESHOLD_SPEC)
        p2_min = thresholds["model3.threshold.p2_min_score"]
        p3_min = thresholds["model3.threshold.p3_min_score"]

        vendor_category = {v.id: v.category for v in session.query(Vendor).all()}
        df = score_vendors(session=session, as_of=as_of)
        df["category"] = df["vendor_id"].map(vendor_category)

        def bucket_for(row):
            if row["category"] == VendorCategory.MUST_PAY:
                return PriorityBucket.P0.value
            if row["category"] == VendorCategory.COMMITMENT:
                return PriorityBucket.P1.value
            if row["score"] > p2_min:
                return PriorityBucket.P2.value
            if row["score"] >= p3_min:
                return PriorityBucket.P3.value
            return PriorityBucket.P4.value

        df["bucket"] = df.apply(bucket_for, axis=1)
        if owns_session:
            session.commit()  # persist any newly-seeded config rows
        return df
    finally:
        if owns_session:
            session.close()


def _distribute_within_bucket(vendor_rows, amount_available):
    """Score-ordered, highest first: fully fund until the amount runs out,
    partially fund the cutoff vendor, everyone after gets zero."""
    allocations = []
    funds_left = amount_available
    for r in sorted(vendor_rows, key=lambda row: -row["score"]):
        need = r["outstanding_balance"]
        # DEFAULT — confirm with Sarath: docs/05 never addresses a vendor
        # carrying a credit (negative outstanding_balance) or an already-zero
        # balance. Either way nothing is owed to them right now, so they get
        # exactly 0 — checked before funds_left, since a negative `need`
        # would otherwise fall through to the `elif` below as a negative
        # allocation, which *adds* money back into funds_left and lets every
        # vendor after them benefit from funds that were never really there.
        if need <= 0:
            allocated, status = 0.0, AllocationStatus.ZERO.value
        elif funds_left <= 0:
            allocated, status = 0.0, AllocationStatus.ZERO.value
        elif funds_left >= need - _MONEY_EPSILON:
            # Cap at `need` (not funds_left) so float drift from repeated
            # subtraction across many vendors can't compound down the list.
            allocated, status = need, AllocationStatus.FULL.value
        else:
            allocated, status = funds_left, AllocationStatus.PARTIAL.value
        assert allocated <= max(need, 0.0) + _MONEY_EPSILON, f"{r['erp_code']}: allocation exceeds outstanding balance"
        funds_left -= allocated
        allocations.append({**r, "allocated_amount": allocated, "status": status})
    return allocations


def generate_plan(available_funds, session=None, as_of=None, eligible_vendor_ids=None):
    """eligible_vendor_ids: optional set of vendor ids to restrict the vendor
    set to (used by regeneration — docs/06). Defaults to None = all vendors,
    so the initial monthly plan is unaffected. Vendors outside this set are
    dropped before any bucket/shortfall math runs, not computed and then
    discarded — an excluded vendor must never consume part of the funds
    pool internally.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        target_pct = _seed_and_read(session, TARGET_PCT_SPEC)
        bucketed = assign_buckets(session=session, as_of=as_of)
        if eligible_vendor_ids is not None:
            bucketed = bucketed[bucketed["vendor_id"].isin(eligible_vendor_ids)]
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        vendor_by_id = {v.id: v for v in vendors}

        # P0/P1 — always fully funded first (CLAUDE.md rule 5), via Model 2's required_amount().
        guaranteed = []
        for row in bucketed[bucketed["bucket"].isin([PriorityBucket.P0.value, PriorityBucket.P1.value])].to_dict(
            "records"
        ):
            vendor = vendor_by_id[row["vendor_id"]]
            amount, rule = required_amount(vendor, ledger_by_vendor[vendor.id], as_of=as_of)
            guaranteed.append({**row, "required_amount": amount, "rule": rule})
        guaranteed_total = sum(r["required_amount"] for r in guaranteed)
        remaining = available_funds - guaranteed_total
        escalation = remaining < 0  # same escalation pattern as Model 2 — flag, don't shrink P0/P1
        funds_for_normal = max(remaining, 0.0)

        bucket_rows = {b: bucketed[bucketed["bucket"] == b].to_dict("records") for b in BUCKET_ORDER}
        # DEFAULT — confirm with Sarath: a Normal vendor's "own total need" for
        # bucket-target purposes is their full outstanding balance (paying
        # them "in full" means clearing the whole balance), not Model 2's
        # narrower oldest-bucket minimum-funds figure — the two models are
        # answering different questions from the same underlying data.
        bucket_need = {b: sum(r["outstanding_balance"] for r in bucket_rows[b]) for b in BUCKET_ORDER}
        combined_need = sum(bucket_need.values())

        # Ledger integrity (CLAUDE.md rule 1) still applies to P0/P1 —
        # required_amount() is fixed and does NOT shrink as the vendor gets
        # paid down this cycle, so it can end up above the vendor's real
        # remaining headroom (outstanding_balance). Cap at headroom and
        # relabel PARTIAL when capped — GUARANTEED must never silently mean
        # "got less than promised" (CLAUDE.md rule 5's whole point).
        # guaranteed_total above stays the uncapped required_amount sum
        # (escalation is a separate, funds-pool-level concept, untouched
        # here) — only allocated_amount per row changes.
        allocations = []
        for r in guaranteed:
            allocated = r["required_amount"]
            status = AllocationStatus.GUARANTEED.value
            if allocated > r["outstanding_balance"] + _MONEY_EPSILON:
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

        if funds_for_normal >= combined_need:
            # Enough to pay P2+P3+P4 in full — skip the percentage-target
            # mechanism entirely rather than relying on it to arrive at 100%.
            for b in BUCKET_ORDER:
                allocations.extend(_distribute_within_bucket(bucket_rows[b], bucket_need[b]))
        else:
            funds_left = funds_for_normal
            target_spend = {}
            for b in BUCKET_ORDER:
                target_amount = bucket_need[b] * target_pct[f"model3.target_pct.{b.lower()}"]
                target_spend[b] = min(target_amount, funds_left)
                funds_left -= target_spend[b]

            topup_spend = {b: 0.0 for b in BUCKET_ORDER}
            if funds_left > 0:
                for b in BUCKET_ORDER:
                    if funds_left <= 0:
                        break
                    topup_spend[b] = min(bucket_need[b] - target_spend[b], funds_left)
                    funds_left -= topup_spend[b]

            for b in BUCKET_ORDER:
                allocations.extend(_distribute_within_bucket(bucket_rows[b], target_spend[b] + topup_spend[b]))

        result = {
            "available_funds": available_funds,
            "guaranteed_total": guaranteed_total,
            "escalation": escalation,
            "escalation_shortfall": max(-remaining, 0.0),
            "bucket_need": bucket_need,
            "allocations": pd.DataFrame(allocations),
        }
        if owns_session:
            session.commit()  # persist any newly-seeded config rows
        return result
    finally:
        if owns_session:
            session.close()
