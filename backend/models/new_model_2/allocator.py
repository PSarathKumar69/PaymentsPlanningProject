"""New Model 2 — Priority-Bucket Ceiling Allocation (docs/14-new-model-2.md).

Built ALONGSIDE New Model 1 and Models 1/2/3 (CLAUDE.md/this task's own
guardrail: none of those get touched). Available funds is the same
two-scenario rule as every other model (docs/14): if funds cover every
unlocked vendor's full 100%, that's what they get — bucket ceilings never
come into it. Only once funds fall short of that does the model fall back
to trying every unlocked vendor at their own priority bucket's ceiling
percentage of required_amount; if even that doesn't fit, cutting happens
in fixed 5% steps, cycling lowest-priority bucket first, with each bucket
dropping out of the rotation once it hits its own floor.

Must Pay/Commitment (P0/P1) — Configuration-tab P0/P1-ceiling task: no
longer a hardcoded "always 100%, bypass the bucket math" special case.
They're two more rows in the SAME PriorityBucket table/rotation as
P2-P5, pinned by default at the top of bucket_order (rotation_position
-2/-1, funded first / cut last, per CLAUDE.md rule 5) but otherwise going
through the identical ceiling/floor/cutting/leftover-topup math below.
Seeded at ceiling=floor=100% by default — at that default, 100%/100% means
the cutting rotation can never actually reduce them below their required
amount (any attempted cut immediately re-clamps to floor==ceiling), which
reproduces the old unconditional-guarantee behavior exactly. Only an
explicit Finance edit lowering P0/P1's floor below 100% lets a severe
shortfall actually cut into them.

Guaranteed-tier IDENTITY (pinned-role task, 2026-08-07): which row IS the
Must Pay/Commitment tier is now `PriorityBucket.pinned_role`
("must_pay"/"commitment"/NULL), not the literal bucket_key text "P0"/"P1".
Finance can freely rename bucket_key/category_name/rotation_position on a
pinned row (backend/configuration/priority_bucket_edits.py's
update_bucket()/reorder_buckets() no longer refuse this) and the
guaranteed-funding behavior below follows the row, not the old name —
remove_bucket() is the one guard left, since deleting a pinned row would
remove the guarantee itself, not just relabel it.

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

# Seed rows for the PriorityBucket table's first-ever read (docs/14: "P0 >=
# P1 >= P2 >= P3 >= P4 >= P5 >= ...") — (ceiling_pct, floor_pct,
# display_label, rotation_position, category_name, pinned_role). Lower
# rotation_position = higher priority (funded first, cut last); the cut
# rotation itself is this list's exact reverse.
#
# P0/P1 (Must Pay/Commitment, CLAUDE.md rule 5 + P0/P1-ceiling task): start
# at rotation_position -2/-1 — strictly ahead of P2's 0, so a fresh seed
# always sorts them first in bucket_order without needing to renumber any
# existing P2-P5 row already seeded in a real DB. Seeded at 100%/100% so a
# fresh read of an already-populated table changes nothing until Finance
# edits them (see module docstring). Their pinned_role
# ("must_pay"/"commitment") is what `_seed_and_read_buckets()` actually
# checks before re-seeding, and what generate_plan() below checks for
# guaranteed-tier membership — bucket_key/category_name/rotation_position
# are all ordinary Finance-editable fields on these two rows now
# (priority_bucket_edits.py), only pinned_role never changes.
#
# P5 (Inactive) = 50% ceiling / 10% floor — confirmed with Finance
# 2026-07-24 (docs/14 "Bucket ceilings"), not a continuation-of-pattern
# guess. P5 is the lowest-priority bucket, cut first in a shortfall.
#
# category_name (Configuration-tab-rebuild task): several rows can share
# one category — P2/P3/P4 are all Normal, each its own ceiling/floor
# tier (docs/14) — so this is a plain label, not a unique key. This is
# what lets the rest of the codebase (column_mapping.py, vendor_edits.py,
# the frontend's Category dropdown) resolve "what category does this
# bucket belong to" from the DB instead of a hardcoded P5<->Inactive link.
#
# Uses VendorCategory's own .value (lowercase "normal"/"inactive"), NOT
# the Title-Case Excel display label ("Normal"/"Inactive") — category_name
# IS the literal value written to Vendor.category (vendor_edits.py,
# column_mapping.py), and every existing real vendor row already stores
# that lowercase form (db/session.py's _normalize_category_values()). A
# mismatch here would make the Configuration dropdown's "Normal" option a
# DIFFERENT string from the 400+ existing vendors already categorized
# "normal", silently splitting the category in two.
_BUCKET_SEED = {
    "P0": (1.0, 1.0, "P0", -2, VendorCategory.MUST_PAY.value, VendorCategory.MUST_PAY.value),
    "P1": (1.0, 1.0, "P1", -1, VendorCategory.COMMITMENT.value, VendorCategory.COMMITMENT.value),
    "P2": (0.95, PCT_FLOOR, "P2", 0, VendorCategory.NORMAL.value, None),
    "P3": (0.90, PCT_FLOOR, "P3", 1, VendorCategory.NORMAL.value, None),
    "P4": (0.85, PCT_FLOOR, "P4", 2, VendorCategory.NORMAL.value, None),
    "P5": (0.50, 0.10, "P5", 3, VendorCategory.INACTIVE.value, None),
}


def _seed_and_read_buckets(session):
    """Bucket order + ceiling/floor/category/pinned-role lookups, DB-backed
    (docs/14 Task 2 Part C) — seeds any _BUCKET_SEED row not yet present
    (idempotent: a brand-new table gets all six; a real DB that already had
    P2-P5 from before the P0/P1-ceiling task just gets those two
    backfilled) and always reads back from the table itself afterward, so
    an edit/add/remove made through priority_bucket_edits.py is picked up
    on the very next call with no code change.

    A pinned row (P0/P1) is matched by its `pinned_role`
    ("must_pay"/"commitment"), not its bucket_key text (pinned-role task,
    2026-08-07) — this is what stops a Finance rename from spawning a
    phantom duplicate default row: once a row's pinned_role is set, no
    later call re-seeds a fresh "P0"/"P1" row for that role, no matter what
    the renamed row's own bucket_key/category_name/rotation_position now
    are. Non-pinned rows are still matched by bucket_key, same as before.

    Also backfills `category_name` for any row that predates that column
    (a fresh ALTER TABLE ADD COLUMN leaves it NULL) — falls back to
    _BUCKET_SEED's label for a recognized key, else the bucket_key itself,
    so it's never left unset for downstream category-lookup code. flush
    (not commit): makes seeded/backfilled rows visible to the query below
    without committing a caller-supplied session's unrelated pending
    changes — same convention as Model 3's own _seed_and_read.

    Returns (bucket_order, ceiling, floor, category_name, pinned_role):
    bucket_order is a plain list of bucket_key strings ordered by
    rotation_position; the rest are {bucket_key: value} dicts (pinned_role
    values are "must_pay"/"commitment"/None).
    """
    existing_keys = {key for (key,) in session.query(PriorityBucket.bucket_key).all()}
    existing_pinned_roles = {role for (role,) in session.query(PriorityBucket.pinned_role).all() if role}
    for key, (ceiling, floor, label, position, category, pinned_role) in _BUCKET_SEED.items():
        if pinned_role:
            already_seeded = pinned_role in existing_pinned_roles
        else:
            already_seeded = key in existing_keys
        if not already_seeded:
            session.add(
                PriorityBucket(
                    bucket_key=key,
                    display_label=label,
                    ceiling_pct=ceiling,
                    floor_pct=floor,
                    rotation_position=position,
                    category_name=category,
                    pinned_role=pinned_role,
                )
            )
    session.flush()
    rows = session.query(PriorityBucket).order_by(PriorityBucket.rotation_position).all()
    for r in rows:
        if not r.category_name:
            r.category_name = _BUCKET_SEED.get(r.bucket_key, (None, None, None, None, r.bucket_key))[4]
    session.flush()
    bucket_order = [r.bucket_key for r in rows]
    ceiling = {r.bucket_key: float(r.ceiling_pct) for r in rows}
    floor = {r.bucket_key: float(r.floor_pct) for r in rows}
    category_name = {r.bucket_key: r.category_name for r in rows}
    pinned_role = {r.bucket_key: r.pinned_role for r in rows}
    return bucket_order, ceiling, floor, category_name, pinned_role


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
        bucket_order, ceiling, floor, _category_name, pinned_role = _seed_and_read_buckets(session)
        fallback_bucket = bucket_order[-1]
        vendor_tag = {v.id: v.priority_tag for v in session.query(Vendor).all()}

        # Guaranteed-tier identity (pinned-role task) — derived fresh from
        # the DB-read pinned_role every call, never a hardcoded "P0"/"P1"
        # literal: pinned_bucket_keys is whichever row(s) currently carry a
        # pinned_role, however Finance has renamed them; bucket_key_by_role
        # maps a vendor's own category value ("must_pay"/"commitment", the
        # pinned_role values themselves) back to that row's current
        # bucket_key.
        pinned_bucket_keys = {key for key, role in pinned_role.items() if role}
        bucket_key_by_role = {role: key for key, role in pinned_role.items() if role}

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
                "category": vendor.category,
                "outstanding_balance": max(
                    float(vendor.opening_balance) - float(vendor.paid_so_far_this_month), 0.0
                ),
                "required_amount": amount,
                "rule": rule,
                # Plain tag as Finance set it, for every vendor regardless of
                # category — read unconditionally now (root-cause fix below).
                # tag is already a plain string (Vendor.priority_tag is a
                # plain String column, an earlier task's fix — no longer a
                # VendorPriorityTag enum instance, so no .value here).
                "priority_tag": vendor_tag.get(vendor.id),
            }
            rows.append(row)

        # effective_bucket resolution — every vendor, guaranteed or not, now
        # flows through the same bucket_order/ceiling/floor math below
        # (P0/P1-ceiling task). CLAUDE.md rule 5: a vendor is in the
        # guaranteed tier if EITHER signal says so — category MUST_PAY/
        # COMMITMENT, or priority_tag matching whichever bucket_key currently
        # carries that pinned_role (pinned-role task — no longer a literal
        # "P0"/"P1" string check) — same OR this codebase has relied on
        # since the original rule-5 fix (real data has Normal-category
        # vendors tagged into the guaranteed tier's bucket, AND Must
        # Pay/Commitment vendors with no tag yet or some other stray tag;
        # both must still land in the guaranteed tier). Category is the
        # authoritative signal for WHICH pinned row once either signal has
        # tripped — an explicit tag matching a pinned bucket_key is honored
        # as-is, a mismatched/missing one defers to category. Only once
        # NEITHER signal says guaranteed does a vendor's own priority_tag
        # (falling back to the lowest-priority bucket if it's unset or
        # unrecognized) decide its bucket, same as before this task.
        for r in rows:
            is_pinned_tier = (
                r["category"] in bucket_key_by_role
                or r["priority_tag"] in pinned_bucket_keys
            )
            if is_pinned_tier:
                tag = r["priority_tag"] if r["priority_tag"] in pinned_bucket_keys else bucket_key_by_role[r["category"]]
            else:
                tag = r["priority_tag"]
                if tag not in ceiling:
                    logger.warning(
                        "%s: no priority_tag set — defaulting to %s (docs/14 defensive fallback; "
                        "shouldn't happen once scripts/seed_priority_tags.py has run)",
                        r["erp_code"],
                        fallback_bucket,
                    )
                    tag = fallback_bucket
                    # Old behavior (pre-P0/P1-ceiling task): the defensive
                    # fallback overwrote the row's own displayed
                    # priority_tag, not just an internal grouping key — kept
                    # exactly for the non-pinned path (pinned/guaranteed rows
                    # never had their displayed tag rewritten either way).
                    r["priority_tag"] = tag
            r["effective_bucket"] = tag

        # guaranteed_total/escalation are reporting-only now (P0/P1-ceiling
        # task) — informational "would Must Pay/Commitment's full required
        # amount alone exceed available funds," independent of whatever
        # ceiling/floor Finance has actually configured for them. The real
        # allocation math below no longer branches on this at all; P0/P1
        # rows are funded (or, if Finance has lowered their floor, cut)
        # through the exact same bucket_pct mechanism as every other row.
        guaranteed_total = sum(r["required_amount"] for r in rows if r["effective_bucket"] in pinned_bucket_keys)
        escalation = available_funds < guaranteed_total
        funds_left = available_funds

        vendor_bucket = {r["vendor_id"]: r["effective_bucket"] for r in rows}
        required_by_vendor = {r["vendor_id"]: r["required_amount"] for r in rows}
        total_required = sum(required_by_vendor.values())

        if total_required <= funds_left + MONEY_EPSILON:
            # Absolute funds (docs/14 "Available funds" — same two-scenario
            # rule as every other model): enough to pay every vendor their
            # full 100%, so bucket ceilings never apply at all — they only
            # exist to soften a real shortfall, not cap a vendor below 100%
            # when funds don't require it.
            bucket_pct = {b: PCT_START for b in bucket_order}
            exceptional_shortfall = False
        else:
            bucket_pct, exceptional_shortfall = _find_bucket_pcts(
                vendor_bucket, required_by_vendor, ceiling, floor, funds_left
            )

        allocations = []
        for r in rows:
            allocated = r["required_amount"] * bucket_pct[r["effective_bucket"]]
            if allocated > r["outstanding_balance"] + MONEY_EPSILON:
                logger.warning(
                    "%s: allocation %.2f would exceed outstanding_balance %.2f — clamping to outstanding_balance",
                    r["erp_code"],
                    allocated,
                    r["outstanding_balance"],
                )
                allocated = max(r["outstanding_balance"], 0.0)
            # P0/P1 keep the distinct "guaranteed" status label regardless
            # of their configured pct (CLAUDE.md rule 5 — they're still
            # their own funding TIER, not just another bucket, even once
            # ceiling/floor are Finance-editable); every other bucket gets
            # the usual full/partial/zero based on where it landed.
            if r["effective_bucket"] in pinned_bucket_keys:
                status = AllocationStatus.GUARANTEED.value
            elif allocated >= r["required_amount"] - MONEY_EPSILON:
                status = AllocationStatus.FULL.value
            elif allocated <= MONEY_EPSILON:
                status = AllocationStatus.ZERO.value
            else:
                status = AllocationStatus.PARTIAL.value
            allocations.append({**r, "allocated_amount": allocated, "status": status})

        # Leftover funds top-up (docs/14) — bucket-percentage cutting moves
        # in fixed 5% steps, so it almost never lands on an exact rupee fit;
        # spend whatever's left over against every vendor's debt instead of
        # discarding it. Nothing here touches bucket_pct itself (bucket_pct
        # still reflects the pre-top-up base percentage — see module
        # docstring/docs/14, individual vendors can now sit above it). At
        # P0/P1's 100%/100% default they're already at their required
        # amount (or their own outstanding-balance cap) before this ever
        # runs, so this is a no-op for them unless Finance has actually
        # lowered their ceiling/floor.
        #
        # Starting `leftover` deliberately mirrors the pre-P0/P1-ceiling-task
        # formula exactly, not a plain available_funds-minus-everything: it
        # clamps the guaranteed tier's own shortfall to zero BEFORE
        # subtracting what Normal/Inactive actually took, same as the old
        # unconditional-guarantee code always did (guaranteed used to be
        # funded from its own uncapped pot; only the leftover AFTER it was
        # ever handed to the Normal/Inactive top-up sweep). Skipping this
        # clamp would double-count an escalation shortfall — once in
        # `escalation_shortfall` above, again here — and change
        # leftover_remaining under a severe shortfall even at the untouched
        # 100%/100% default, which must never happen.
        pinned_allocated = sum(
            r["allocated_amount"] for r in allocations if r["effective_bucket"] in pinned_bucket_keys
        )
        other_allocated = sum(
            r["allocated_amount"] for r in allocations if r["effective_bucket"] not in pinned_bucket_keys
        )
        leftover = max(available_funds - pinned_allocated, 0.0) - other_allocated
        leftover_topup_total, leftover_remaining = _apply_leftover_topup(allocations, leftover, bucket_order)

        # effective_bucket was only ever an internal grouping key for the
        # ceiling/cutting/top-up math above — strip it before this reaches
        # the displayed plan (it's always equal to priority_tag today, but
        # kept as a distinct internal field for symmetry with the rest of
        # this module).
        for r in allocations:
            r.pop("effective_bucket", None)

        if owns_session:
            session.commit()  # persist any newly-seeded config rows
        return {
            "available_funds": available_funds,
            "guaranteed_total": guaranteed_total,
            "escalation": escalation,
            "escalation_shortfall": max(guaranteed_total - available_funds, 0.0),
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
