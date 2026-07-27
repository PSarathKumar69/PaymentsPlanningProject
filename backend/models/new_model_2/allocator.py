"""New Model 2 — Priority-Bucket Ceiling Allocation (docs/14-new-model-2.md).

Built ALONGSIDE New Model 1 and Models 1/2/3 (CLAUDE.md/this task's own
guardrail: none of those get touched). Must Pay/Commitment are funded first
at their full required_amount, identical to New Model 1
(backend/models/new_model_1/allocator.py) — the same pattern, not
reimplemented. Available funds is the same two-scenario rule as every
other model (docs/14): if funds cover every unlocked Normal vendor's full
100%, that's what they get — bucket ceilings never come into it. Only once
funds fall short of that does the model fall back to trying every
unlocked Normal vendor at their own priority bucket's ceiling percentage
of required_amount; if even that doesn't fit, cutting happens in fixed 5%
steps, cycling P4 -> P3 -> P2 -> P4 -> ... (lowest priority cut first),
with each bucket dropping out of the rotation once it hits a 25% floor.

Override/locking is NOT handled here — same as New Model 1: an overridden
vendor is excluded from this function's vendor set entirely by the router
(via eligible_vendor_ids) and their override amount is subtracted from
available_funds before this is ever called.

Inactive vendors (docs/14 — renamed 2026-07-24 from the original "Exit"
category/tag; the old EXIT category/tag and its fold-into-last-bucket
behavior no longer exist anywhere in this codebase): a 4th category,
treated exactly like Normal for required_amount/eligibility. It carries
its own real, Finance-picked priority_tag — P5, and only P5 — and is
grouped by that tag exactly like a Normal vendor; the allocator makes no
distinction between NORMAL and INACTIVE anywhere below. P5 is the
lowest-priority bucket: cut first in a shortfall, its own (lower)
ceiling/floor (see _BUCKET_SEED below).

Bucket definitions themselves (which tags exist, their ceiling/floor, and
their cutting-rotation order) are DB-backed (PriorityBucket table,
docs/11 Task 2 Part C) — read fresh every generate_plan() call via
_seed_and_read_buckets(), never a hardcoded Python list/dict. Adding a
6th bucket, or changing an existing one's ceiling/floor, is a row edit in
that table (via backend/configuration/priority_bucket_edits.py), not a
code change.
"""
import logging
from collections import deque

import pandas as pd

from backend.db.models import PriorityBucket, Vendor
from backend.db.session import SessionLocal
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import AllocationStatus, VendorCategory
from backend.shared.min_funds import _load_vendors_and_ledger
from backend.shared.min_funds_v2 import required_amount_v2

logger = logging.getLogger(__name__)

# Fixed for New Model 2 specifically (docs/14) — same values as New Model
# 1's PCT_START/PCT_STEP/PCT_FLOOR. PCT_STEP (the fixed cut-step size) is
# still a flat constant, not a per-bucket config item (docs/14 never asked
# for a per-bucket step); ceiling/floor ARE per-bucket, DB-backed below.
PCT_START = 1.0
PCT_STEP = 0.05
# PCT_FLOOR: only used today as P2/P3/P4's seeded default floor value (see
# _BUCKET_SEED below) — the actual per-call floor the cutting loop enforces
# always comes from the PriorityBucket table, never this constant directly.
PCT_FLOOR = 0.25

# Seed rows for the PriorityBucket table's first-ever read (docs/14: "P2 >=
# P3 >= P4 >= P5 >= ...") — (ceiling_pct, floor_pct, display_label,
# rotation_position). rotation_position 0 = highest priority (funded first,
# cut last); the cut rotation itself is this list's exact reverse.
#
# P5 (Inactive) = 50% ceiling / 10% floor — confirmed with Finance
# 2026-07-24 (docs/14 "Bucket ceilings"), not a continuation-of-pattern
# guess. P5 is the lowest-priority bucket, cut first in a shortfall.
_BUCKET_SEED = {
    "P2": (0.95, PCT_FLOOR, "P2", 0),
    "P3": (0.90, PCT_FLOOR, "P3", 1),
    "P4": (0.85, PCT_FLOOR, "P4", 2),
    "P5": (0.50, 0.10, "P5", 3),
}


def _seed_and_read_buckets(session):
    """Bucket order + ceiling/floor lookups, DB-backed (docs/14 Task 2 Part
    C) — seeds _BUCKET_SEED's rows once (idempotent, only if the table is
    still empty) and always reads back from the table itself afterward, so
    an edit/add/remove made through priority_bucket_edits.py is picked up
    on the very next call with no code change. flush (not commit): makes
    seeded rows visible to the query below without committing a
    caller-supplied session's unrelated pending changes — same convention
    as Model 3's own _seed_and_read.

    Returns (bucket_order, ceiling, floor): bucket_order is a plain list of
    bucket_key strings ordered by rotation_position; ceiling/floor are
    {bucket_key: fraction} dicts.
    """
    if session.query(PriorityBucket).first() is None:
        for key, (ceiling, floor, label, position) in _BUCKET_SEED.items():
            session.add(
                PriorityBucket(
                    bucket_key=key, display_label=label, ceiling_pct=ceiling, floor_pct=floor, rotation_position=position
                )
            )
        session.flush()
    rows = session.query(PriorityBucket).order_by(PriorityBucket.rotation_position).all()
    bucket_order = [r.bucket_key for r in rows]
    ceiling = {r.bucket_key: float(r.ceiling_pct) for r in rows}
    floor = {r.bucket_key: float(r.floor_pct) for r in rows}
    return bucket_order, ceiling, floor


def _find_bucket_pcts(vendor_bucket, required_by_vendor, ceiling, floor, funds_left):
    """Highest-fitting per-bucket percentages, starting from `ceiling` and
    cutting in fixed PCT_STEP steps, cycling the cut rotation (docs/14
    rules 3-6) — lowest-priority bucket first, i.e. `ceiling`'s own key
    order reversed (ceiling is always built from bucket_order, ascending
    priority, by the caller). `floor` is a per-bucket {bucket_key: fraction}
    dict (docs/14 Task 2 Part C — no longer one flat PCT_FLOOR for every
    bucket). Returns (bucket_pct, exceptional_shortfall) — bucket_pct is
    always fully populated (even under exceptional_shortfall, every bucket
    sits at its own floor at that point) for best-effort display, same
    convention New Model 1's effective_pct fallback uses.
    """
    bucket_pct = dict(ceiling)

    def total_cost():
        return sum(required_by_vendor[v] * bucket_pct[vendor_bucket[v]] for v in required_by_vendor)

    if total_cost() <= funds_left + MONEY_EPSILON:
        return bucket_pct, False

    rotation = deque(reversed(list(ceiling)))
    while total_cost() > funds_left + MONEY_EPSILON:
        if not rotation:
            return bucket_pct, True  # every bucket at its own floor and it still doesn't fit
        bucket = rotation[0]
        bucket_pct[bucket] = round(bucket_pct[bucket] - PCT_STEP, 10)
        if bucket_pct[bucket] <= floor[bucket] + 1e-9:
            bucket_pct[bucket] = floor[bucket]
            rotation.popleft()  # dropped out of rotation for good (docs/14 rule 5)
        else:
            rotation.rotate(-1)  # move to the back — lowest priority -> ... -> lowest priority -> ... (docs/14 rule 4)
    return bucket_pct, False


# Pure-unit-test convenience only (P2/P3/P4, no P5) — generate_plan() below
# always passes the real DB-read bucket_order explicitly; nothing in the
# actual generate-plan code path relies on this default.
_DEFAULT_TEST_BUCKET_ORDER = ("P2", "P3", "P4")


def _apply_leftover_topup(normal_allocations, leftover, bucket_order=_DEFAULT_TEST_BUCKET_ORDER):
    """docs/14 'Leftover funds top-up (post-allocation)' — forward sweep in
    `bucket_order` (highest priority first), never backward.
    `normal_allocations` is the list of Normal/Inactive-vendor allocation
    dicts (mutated in place: allocated_amount and status only); guaranteed
    rows are never passed in, so they're never touched. Returns (spent,
    leftover_after) — spent is the leftover this step actually paid out.

    Grouped by `effective_bucket` (== `priority_tag` for both Normal and
    Inactive rows today) rather than `priority_tag` directly, matching the
    main allocation loop above's own grouping key.

    One pass = the bucket's vendors in vendor_id order; a vendor eligible
    for one more PCT_STEP is bumped once per pass (never twice in the same
    pass). A pass that pays nobody permanently retires the bucket — a
    bucket that can't be satisfied now can't be satisfied later with less
    leftover, so buckets are never revisited (docs/14 rule 3).
    """
    spent = 0.0
    if leftover <= MONEY_EPSILON:
        return spent, leftover

    by_bucket = {b: [] for b in bucket_order}
    for r in normal_allocations:
        by_bucket[r["effective_bucket"]].append(r)
    for rows in by_bucket.values():
        rows.sort(key=lambda r: r["vendor_id"])  # fixed, deterministic order (docs/14)

    for bucket in bucket_order:  # highest priority -> lowest, forward sweep only
        rows = by_bucket[bucket]
        while True:
            paid_any = False
            for r in rows:
                cap = min(r["required_amount"], r["outstanding_balance"])
                if r["allocated_amount"] >= cap - MONEY_EPSILON:
                    continue  # already at 100% or at their outstanding-balance ceiling
                step_amount = r["required_amount"] * PCT_STEP
                if leftover < step_amount - MONEY_EPSILON:
                    continue  # doesn't fit this vendor's next step — try the rest of the bucket
                new_allocated = r["allocated_amount"] + step_amount
                if new_allocated > cap + MONEY_EPSILON:
                    logger.warning(
                        "%s: leftover top-up %.2f would exceed its cap %.2f — clamping",
                        r["erp_code"],
                        new_allocated,
                        cap,
                    )
                    new_allocated = max(cap, 0.0)
                actual_step = new_allocated - r["allocated_amount"]
                r["allocated_amount"] = new_allocated
                leftover -= actual_step
                spent += actual_step
                if new_allocated >= r["required_amount"] - MONEY_EPSILON:
                    r["status"] = AllocationStatus.FULL.value
                elif new_allocated <= MONEY_EPSILON:
                    r["status"] = AllocationStatus.ZERO.value
                else:
                    r["status"] = AllocationStatus.PARTIAL.value
                paid_any = True
            if not paid_any:
                break  # bucket retired permanently — move on to the next one
    return spent, leftover


def generate_plan(available_funds, session=None, as_of=None, eligible_vendor_ids=None):
    """Same shape/signature as New Model 1's generate_plan() (see that
    module's docstring — not re-explained here): runs identically for the
    first generation of the month and every regeneration after it, no
    internal first-vs-regeneration branching (docs/14).
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        bucket_order, ceiling, floor = _seed_and_read_buckets(session)
        fallback_bucket = bucket_order[-1]
        vendor_tag = {v.id: v.priority_tag for v in session.query(Vendor).all()}

        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        if eligible_vendor_ids is not None:
            vendors = [v for v in vendors if v.id in eligible_vendor_ids]

        rows = []
        for vendor in vendors:
            amount, rule = required_amount_v2(vendor, ledger_by_vendor[vendor.id], as_of=as_of)
            row = {
                "vendor_id": vendor.id,
                "erp_code": vendor.erp_code,
                "vendor_name": vendor.vendor_name,
                "category": vendor.category.value,
                "outstanding_balance": max(
                    float(vendor.opening_balance) - float(vendor.paid_so_far_this_month), 0.0
                ),
                "required_amount": amount,
                "rule": rule,
            }
            if vendor.category in (VendorCategory.NORMAL, VendorCategory.INACTIVE):
                # Inactive (docs/14, renamed 2026-07-24 from Exit) is grouped
                # exactly like Normal — its own real, Finance-picked
                # priority_tag (P5 for Inactive), no folding into any other
                # bucket.
                tag = vendor_tag.get(vendor.id)
                if tag is None:
                    logger.warning(
                        "%s: no priority_tag set — defaulting to %s (docs/14 defensive fallback; "
                        "shouldn't happen once scripts/seed_priority_tags.py has run)",
                        vendor.erp_code,
                        fallback_bucket,
                    )
                    row["priority_tag"] = fallback_bucket
                else:
                    # tag is already a plain string (Vendor.priority_tag is a
                    # plain String column, this task's fix — no longer a
                    # VendorPriorityTag enum instance, so no .value here).
                    row["priority_tag"] = tag
                # effective_bucket is what allocation math groups by; for a
                # real Normal/Inactive vendor it's just their own priority_tag.
                row["effective_bucket"] = row["priority_tag"]
            rows.append(row)

        guaranteed = [
            r for r in rows if r["category"] in (VendorCategory.MUST_PAY.value, VendorCategory.COMMITMENT.value)
        ]
        # "normal" here means "the allocation-eligible pool" — Normal AND
        # Inactive vendors both flow through the same bucket-ceiling/
        # cutting/leftover-topup math below (docs/14); only effective_bucket
        # (== priority_tag for both) decides which bucket each row is
        # grouped into.
        normal = [r for r in rows if r["category"] in (VendorCategory.NORMAL.value, VendorCategory.INACTIVE.value)]

        guaranteed_total = sum(r["required_amount"] for r in guaranteed)
        remaining = available_funds - guaranteed_total
        # Per CLAUDE.md rule 5, Must Pay/Commitment are always fully funded —
        # even if that pushes remaining below zero. Flag, don't shrink them.
        escalation = remaining < 0
        funds_left = max(remaining, 0.0)

        vendor_bucket = {r["vendor_id"]: r["effective_bucket"] for r in normal}
        required_by_vendor = {r["vendor_id"]: r["required_amount"] for r in normal}
        total_required_normal = sum(required_by_vendor.values())

        if total_required_normal <= funds_left + MONEY_EPSILON:
            # Absolute funds (docs/14 "Available funds" — same two-scenario
            # rule as every other model): enough to pay every Normal vendor
            # their full 100%, so bucket ceilings never apply at all — they
            # only exist to soften a real shortfall, not cap a vendor below
            # 100% when funds don't require it.
            bucket_pct = {b: PCT_START for b in bucket_order}
            exceptional_shortfall = False
        else:
            bucket_pct, exceptional_shortfall = _find_bucket_pcts(
                vendor_bucket, required_by_vendor, ceiling, floor, funds_left
            )

        allocations = []
        # Ledger integrity (CLAUDE.md rule 1) — same headroom-cap-and-relabel
        # pattern every other model already uses for its guaranteed tier.
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

        normal_allocations = []
        for r in normal:
            allocated = r["required_amount"] * bucket_pct[r["effective_bucket"]]
            if allocated > r["outstanding_balance"] + MONEY_EPSILON:
                logger.warning(
                    "%s: allocation %.2f would exceed outstanding_balance %.2f — clamping to outstanding_balance",
                    r["erp_code"],
                    allocated,
                    r["outstanding_balance"],
                )
                allocated = max(r["outstanding_balance"], 0.0)
            if allocated >= r["required_amount"] - MONEY_EPSILON:
                status = AllocationStatus.FULL.value
            elif allocated <= MONEY_EPSILON:
                status = AllocationStatus.ZERO.value
            else:
                status = AllocationStatus.PARTIAL.value
            normal_allocations.append({**r, "allocated_amount": allocated, "status": status})

        # Leftover funds top-up (docs/14) — bucket-percentage cutting moves
        # in fixed 5% steps, so it almost never lands on an exact rupee fit;
        # spend whatever's left over against Normal vendors' debt instead of
        # discarding it. Nothing here touches guaranteed rows or bucket_pct
        # itself (bucket_pct still reflects the pre-top-up base percentage —
        # see module docstring/docs/14, individual vendors can now sit above
        # it).
        leftover = funds_left - sum(r["allocated_amount"] for r in normal_allocations)
        leftover_topup_total, leftover_remaining = _apply_leftover_topup(normal_allocations, leftover, bucket_order)

        # effective_bucket was only ever an internal grouping key for the
        # ceiling/cutting/top-up math above — strip it before this reaches
        # the displayed plan (it's always equal to priority_tag today, but
        # kept as a distinct internal field for symmetry with the rest of
        # this module).
        for r in normal_allocations:
            r.pop("effective_bucket", None)
        allocations.extend(normal_allocations)

        if owns_session:
            session.commit()  # persist any newly-seeded config rows
        return {
            "available_funds": available_funds,
            "guaranteed_total": guaranteed_total,
            "escalation": escalation,
            "escalation_shortfall": max(-remaining, 0.0),
            "bucket_ceiling": ceiling,
            "bucket_pct": bucket_pct,
            "exceptional_shortfall": exceptional_shortfall,
            "leftover_topup_total": leftover_topup_total,
            "leftover_remaining": leftover_remaining,
            "allocations": pd.DataFrame(allocations),
        }
    finally:
        if owns_session:
            session.close()


if __name__ == "__main__":
    result = generate_plan(available_funds=1_000_000_000)
    pd.set_option("display.width", 200)
    print(f"bucket_pct={result['bucket_pct']} exceptional={result['exceptional_shortfall']}")
    print(
        result["allocations"][
            ["erp_code", "vendor_name", "category", "required_amount", "allocated_amount", "status"]
        ].head(10)
    )
