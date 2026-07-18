"""Model 2 — Minimum Funds Advisory (docs/04-model2-min-funds-advisory.md).

required_amount()/calculate_minimum_funds_required() moved to
backend/shared/min_funds.py — Model 1 (now the sole driver of
payment_status/cycle_allocation) needs them without depending on this
package at all, and Model 2 is slated for removal later. Re-imported here
(not reimplemented) so this module's own generate_plan() keeps behaving
exactly as before, and so Model 3/regeneration/planner's existing imports
of `_load_vendors_and_ledger`/`required_amount` FROM this module keep
working unchanged.

Two distinct, independently-triggerable actions — a hard sequencing rule,
not a preference. Both call the same required_amount() so the numbers
can't drift apart:

  - calculate_minimum_funds_required(): shown to Finance BEFORE any funds
    figure is entered or usable.
  - generate_plan(available_funds): runs only after Finance has entered
    that figure.
"""
import logging

import pandas as pd

from backend.db.session import SessionLocal
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import AllocationStatus, VendorCategory
from backend.shared.min_funds import (
    _load_vendors_and_ledger,
    _vendor_required_rows,
    calculate_minimum_funds_required,
    required_amount,
)

logger = logging.getLogger(__name__)

__all__ = [
    "required_amount",
    "calculate_minimum_funds_required",
    "generate_plan",
]


def generate_plan(available_funds, session=None, as_of=None, eligible_vendor_ids=None):
    """Step 4 of docs/04's flow. Must run after Finance has entered available_funds.

    eligible_vendor_ids: optional set of vendor ids to restrict the vendor
    set to (used by regeneration — docs/06). Defaults to None = all vendors,
    so the initial monthly plan is unaffected. Vendors outside this set are
    dropped from the vendor set before any funds math runs, not computed
    and then discarded — an excluded vendor must never consume part of the
    funds pool internally.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        if eligible_vendor_ids is not None:
            vendors = [v for v in vendors if v.id in eligible_vendor_ids]
        rows = _vendor_required_rows(vendors, ledger_by_vendor, as_of=as_of)

        guaranteed = [r for r in rows if r["category"] != VendorCategory.NORMAL.value]
        normal = [r for r in rows if r["category"] == VendorCategory.NORMAL.value]

        guaranteed_total = sum(r["required_amount"] for r in guaranteed)
        remaining = available_funds - guaranteed_total
        # Per CLAUDE.md rule 5, Must Pay/Commitment are always fully funded —
        # even if that pushes remaining below zero. Flag, don't shrink them.
        escalation = remaining < 0

        # DEFAULT — confirm with Sarath: Normal vendors ordered by precise
        # day-level age of their oldest tranche (oldest first), tie-broken
        # by that bucket's outstanding amount (largest first).
        normal.sort(key=lambda r: (-r["oldest_bucket_age_days"], -r["required_amount"]))

        # Ledger integrity (CLAUDE.md rule 1) still applies to Must Pay/
        # Commitment vendors — required_amount() is fixed and does NOT
        # shrink as the vendor gets paid down this cycle, so it can end up
        # above the vendor's real remaining headroom (outstanding_balance).
        # Cap at headroom, same isolate-and-continue + warning pattern as
        # the Normal loop below, and relabel PARTIAL when capped —
        # GUARANTEED must never silently mean "got less than promised"
        # (CLAUDE.md rule 5's whole point). guaranteed_total above stays the
        # uncapped required_amount sum (escalation is a separate,
        # funds-pool-level concept, untouched here) — only allocated_amount
        # per row changes.
        # .value (plain str), not the enum member: these feed straight into
        # a DataFrame column, and pandas' string dtype does a strict
        # type(x) is str check in vectorized comparisons — a str-subclass
        # enum member matches fine scalar-to-scalar but fails Series-wide.
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

        funds_left = max(remaining, 0.0)
        for r in normal:
            if funds_left <= 0:
                allocations.append({**r, "allocated_amount": 0.0, "status": AllocationStatus.ZERO.value})
                continue
            if funds_left >= r["required_amount"]:
                allocated, status = r["required_amount"], AllocationStatus.FULL.value
            else:
                allocated, status = funds_left, AllocationStatus.PARTIAL.value
            # DEFAULT — confirm with Sarath: isolate-and-continue, not crash
            # the whole function. required_amount is derived from the same
            # ledger as outstanding_balance and should never disagree with
            # it enough to overpay — but if one vendor's data is bad or
            # inconsistent enough that this would, clamp that one vendor at
            # their real outstanding_balance (never below 0 — a credit
            # balance still owes nothing) and keep computing everyone else's
            # allocation, rather than aborting Finance's entire plan over
            # one vendor's bad row.
            if allocated > r["outstanding_balance"] + MONEY_EPSILON:
                logger.warning(
                    "%s: allocation %.2f would exceed outstanding_balance %.2f — clamping to outstanding_balance",
                    r["erp_code"],
                    allocated,
                    r["outstanding_balance"],
                )
                allocated = max(r["outstanding_balance"], 0.0)
                status = (
                    AllocationStatus.FULL.value
                    if allocated >= r["required_amount"] - MONEY_EPSILON
                    else AllocationStatus.PARTIAL.value
                )
            funds_left -= allocated
            allocations.append({**r, "allocated_amount": allocated, "status": status})

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
