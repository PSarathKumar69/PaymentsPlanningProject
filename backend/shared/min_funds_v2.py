"""New Model 2's own Minimum Funds Required rule (docs/14-new-model-2.md,
"Minimum Funds Required" + "Planning month (as_of)" sections).

Deliberately separate from backend/shared/min_funds.py's required_amount()/
_carry_forward_normal_amount() — those are shared by Models 1/2/3 and New
Model 1 (still live until those are deleted) and stay completely untouched
per this task's own scope guardrail (CLAUDE.md). New Model 2's rule is
structurally different (capped at exactly two months' worth of bills —
oldest, plus the next month after it with a real balance, never a running
carry-forward of every leftover tranche), so it gets its own module rather
than branching inside the old one.

CORRECTED (this task): the "second month" paired with the oldest is NOT
always the current (0-30) bucket — it's whichever month, walking forward
from the oldest, is the first to still carry a nonzero balance (skipping
any zero-balance month in between). That only happens to be the current
month when nothing between them is outstanding. The previous version of
this file always paired oldest with current directly, which was wrong
whenever a real gap month sat between them.

Must Pay, Normal, and Inactive vendors (docs/14) all share this exact rule now
— Must Pay's old "last month's own payable" formula is gone for New Model
2 specifically (backend/shared/min_funds.py's own Must Pay branch is
unchanged and still used by Models 1/2/3/New Model 1). Commitment is
excluded — its own untouched opening-balance/commitment_months formula is
delegated to min_funds.required_amount() directly below, so the number can
never drift between the old and new code paths.
"""

import pandas as pd

from backend.db.session import SessionLocal
from backend.shared.aging import build_vendor_tranches
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import VendorCategory
from backend.shared.min_funds import _load_vendors_and_ledger, required_amount


def _compute_v2(vendor, ledger_rows, as_of, paid_so_far_override):
    """The one place docs/14's Minimum Funds Required rule is implemented —
    required_amount_v2() and min_funds_breakdown_v2() both derive from this,
    so the total and its vendor-detail-card explanation can never disagree.

    `as_of` is the "current" (0-30) reference month — New Model 2's own
    planning-month-minus-one-calendar-month (see
    backend/api/routers/new_model_2.py's _month_minus_one()), never today's
    real calendar date. Defaults to the vendor's own latest ledger month,
    same convention backend/shared/aging.py's compute_vendor_aging() uses,
    for callers/tests that don't pass one explicitly.
    """
    paid_so_far = float(vendor.paid_so_far_this_month) if paid_so_far_override is None else float(paid_so_far_override)

    if vendor.category == VendorCategory.COMMITMENT:
        amount, rule = required_amount(vendor, ledger_rows, as_of=as_of, paid_so_far_override=paid_so_far_override)
        return {
            "total": amount,
            "rule": rule,
            "category": vendor.category,
            "opening_balance": float(vendor.opening_balance),
            "live_balance": max(float(vendor.opening_balance) - paid_so_far, 0.0),
            "commitment_months": vendor.commitment_months or 1,
            "current_month": None,
            "current_amount": None,
            "oldest_month": None,
            "oldest_amount": None,
            "second_month": None,
            "second_amount": None,
        }

    as_of = as_of or (ledger_rows[-1].month if ledger_rows else None)
    tranches = build_vendor_tranches(ledger_rows, already_paid_this_cycle=paid_so_far)
    outstanding = [t for t in tranches if t.remaining > MONEY_EPSILON]

    # Defensive cap (this task, same principle as allocator.py's own rule-1
    # ledger-integrity clamp): build_vendor_tranches() only ever creates a
    # tranche for a POSITIVE payable row, and separately drops any leftover
    # payment below MONEY_EPSILON as noise rather than carrying it forward —
    # so a real negative-payable credit/correction (confirmed real case:
    # V00393, a -8,088.69 correction) or an accumulated sub-rupee rounding
    # drift (confirmed real case: V00986, ~-3.20 over several months) never
    # nets against the tranche list, even though it's already correctly
    # reflected in the vendor's real running ledger balance
    # (vendor.opening_balance). Without this cap, required_amount_v2() can
    # hand back a figure the vendor could never actually owe — never
    # silently trusted past what they really do.
    live_outstanding = max(float(vendor.opening_balance) - paid_so_far, 0.0)

    def _capped(result):
        result["total"] = min(result["total"], live_outstanding)
        return result

    base = {
        "category": vendor.category,
        "opening_balance": None,
        "live_balance": None,
        "commitment_months": None,
    }
    # second_month/second_amount: None unless rule 3 actually fires below —
    # every other branch has at most one "extra" month (or none at all), so
    # there's nothing to name as a distinct "second month" for them.
    no_second = {"second_month": None, "second_amount": None}

    if not outstanding:
        # Rule 6: nothing owed at all.
        return _capped({**base, **no_second, "total": 0.0, "rule": "v2_no_outstanding", "current_month": None,
                "current_amount": None, "oldest_month": None, "oldest_amount": None})

    current = next((t for t in outstanding if t.month == as_of), None)
    current_amount = current.remaining if current else 0.0
    # Strictly older than the current (0-30) reference month — the current
    # month itself is never counted as its own "older" leftover.
    older = [t for t in outstanding if t.month < as_of]

    if not older:
        if current is None:
            # Rule 1b (this task, flagged default — docs/14 doesn't cover
            # this): no tranche in the current (0-30) reference bucket AND
            # no older leftover either — every real outstanding tranche is
            # STRICTLY AFTER as_of. `outstanding` is non-empty here (the
            # "nothing owed at all" case already returned above), so this
            # can only happen when as_of (planning month minus one) predates
            # the vendor's/sheet's very first recorded month — e.g. Finance
            # planning for the sheet's own first month, right after a fresh
            # upload. There is no "current month's own bill" to point to,
            # but nothing was genuinely leftover from before either — a flat
            # 0 here would be wrong (real money is owed) and doesn't match
            # rule 1's spirit ("no older leftover, only the [nearest] bill
            # is outstanding"). Decision: treat the EARLIEST outstanding
            # tranche (the nearest real bill, oldest -> newest per
            # build_vendor_tranches()'s own contract) as the reference
            # figure directly, same as if it were the current month's bill.
            earliest = outstanding[0]
            return _capped({**base, **no_second, "total": earliest.remaining, "rule": "v2_no_history_before_as_of",
                    "current_month": earliest.month, "current_amount": earliest.remaining, "oldest_month": None,
                    "oldest_amount": None})
        # Rule 1: no older leftover at all, only the current month's own bill.
        return _capped({**base, **no_second, "total": current_amount, "rule": "v2_only_current", "current_month": as_of,
                "current_amount": current_amount, "oldest_month": None, "oldest_amount": None})

    # Rule 5: the single oldest month's own remaining balance — `older` is
    # already oldest -> newest (build_vendor_tranches()'s own contract), so
    # the first entry is it. Never a coarse "120+"-style bucket sum, and
    # never a second/third older month even if one also has a balance.
    oldest = older[0]

    if current_amount <= MONEY_EPSILON:
        # Rule 2: current bucket is zero, but an older month is outstanding.
        return _capped({**base, **no_second, "total": oldest.remaining, "rule": "v2_current_zero_oldest_only",
                "current_month": as_of, "current_amount": 0.0, "oldest_month": oldest.month,
                "oldest_amount": oldest.remaining})

    if oldest.remaining <= 0.5 * current_amount + MONEY_EPSILON:  # inclusive: exactly 50% qualifies (docs/14),
        # checked ONCE against the oldest month only — never re-checked
        # against the running two-month sum below (docs/14 rule 3).
        #
        # CORRECTED (this task): the second month is NOT always `current` —
        # it's the first month, walking forward from the oldest, that still
        # carries a nonzero balance. `outstanding` already excludes every
        # zero-balance tranche (filtered above), so the next entry after
        # `oldest` in month order IS that first-nonzero month, whatever
        # calendar gap separates them — a zero-balance month never even
        # appears here to "skip" explicitly.  Guaranteed non-empty: rule 2
        # above already handled current_amount == 0, so current itself is a
        # qualifying (nonzero) candidate at or after this point, meaning the
        # walk can never run past it (docs/14).
        after_oldest = sorted((t for t in outstanding if t.month > oldest.month), key=lambda t: t.month)
        second = after_oldest[0]
        is_current = second.month == as_of
        return _capped({
            **base,
            "total": oldest.remaining + second.remaining,
            "rule": "v2_oldest_and_current" if is_current else "v2_oldest_and_second",
            "current_month": as_of,
            "current_amount": current_amount,
            "oldest_month": oldest.month,
            "oldest_amount": oldest.remaining,
            "second_month": second.month,
            "second_amount": second.remaining,
        })

    # Rule 4: oldest > 50% of current -> oldest only, current not added.
    return _capped({**base, **no_second, "total": oldest.remaining, "rule": "v2_oldest_only", "current_month": as_of,
            "current_amount": current_amount, "oldest_month": oldest.month, "oldest_amount": oldest.remaining})


def required_amount_v2(vendor, ledger_rows, as_of=None, paid_so_far_override=None):
    """New Model 2's required-amount figure (docs/14) — same (amount, rule)
    shape as backend/shared/min_funds.py's required_amount(), for Must Pay/
    Normal/Inactive/Commitment alike, but a structurally different rule for the
    first three (capped at oldest + the next month with a real balance,
    never a running carry-forward of every leftover tranche)."""
    detail = _compute_v2(vendor, ledger_rows, as_of, paid_so_far_override)
    return detail["total"], detail["rule"]


def min_funds_breakdown_v2(vendor, ledger_rows, as_of=None):
    """Per-vendor breakdown for the vendor detail card's "Min Funds
    Required" section (docs/14) — which month(s) got picked and why, not
    just the total. Always live (no paid_so_far_override) — mirrors
    backend/shared/min_funds.py's min_funds_tranche_breakdown()'s job for
    the old rule."""
    return _compute_v2(vendor, ledger_rows, as_of, paid_so_far_override=None)


def min_funds_tranche_breakdown_v2(vendor, ledger_rows, as_of=None):
    """v2 equivalent of min_funds.min_funds_tranche_breakdown() — same
    (total, tranches) return shape (tranches oldest -> newest,
    {month, remaining_amount, is_leftover}), for GET /vendors/aging and
    /vendors/{id}/aging (backend/api/routers/vendors.py) and
    build_weekly_view() (backend/weekly_planning/planner.py), all of which
    used to call the old function and so disagreed with the Planning
    table's own required_amount_v2()-based figure for any vendor whose old
    vs new rule actually differs (confirmed real cases: V00193, V00204,
    V00194, V00393, V00226).

    Built from this module's own _compute_v2() detail dict rather than a
    second calculation — `total` always equals the sum of the tranche
    amounts returned here, by construction (each branch below adds exactly
    the month(s) _compute_v2() actually summed into `total`).

    Must Pay/Commitment: no tranche concept, tranches always [] (same
    convention the old function uses) — total is still their real required
    amount, Must Pay's own last-month's-payable rule or Commitment's
    outstanding-balance rule via required_amount().

    "is_leftover" mirrors the old function's own meaning — True for a
    month that isn't this cycle's own fresh bill (the oldest month, or a
    "second" month that isn't `as_of` itself); False for the current (0-30)
    month's own bill, including the v2_no_history_before_as_of fallback's
    nearest-bill figure, which stands in for a current bill that doesn't
    exist yet at this as_of.
    """
    detail = _compute_v2(vendor, ledger_rows, as_of, paid_so_far_override=None)
    if vendor.category == VendorCategory.COMMITMENT:
        return detail["total"], []

    tranches = []
    if detail["oldest_month"] is not None:
        tranches.append(
            {"month": detail["oldest_month"], "remaining_amount": detail["oldest_amount"], "is_leftover": True}
        )
    if detail["second_month"] is not None:
        is_current = detail["second_month"] == detail["current_month"]
        tranches.append(
            {
                "month": detail["second_month"],
                "remaining_amount": detail["second_amount"],
                "is_leftover": not is_current,
            }
        )
    elif detail["rule"] in ("v2_only_current", "v2_no_history_before_as_of") and detail["current_month"] is not None:
        tranches.append(
            {
                "month": detail["current_month"],
                "remaining_amount": detail["current_amount"],
                "is_leftover": detail["rule"] == "v2_no_history_before_as_of",
            }
        )

    # _compute_v2()'s defensive cap (real confirmed case: V00393) only ever
    # clamps `total` itself — oldest_amount/second_amount/current_amount are
    # the raw, pre-cap tranche figures, so a capped vendor's tranches would
    # otherwise sum to MORE than `total`. Absorb the excess from the oldest
    # tranche forward (same "older/leftover money is the less certain
    # figure" reasoning _capped()'s own docstring uses) so the invariant
    # "tranches always sum to total" holds even for a capped vendor.
    excess = sum(t["remaining_amount"] for t in tranches) - detail["total"]
    if excess > MONEY_EPSILON:
        for t in tranches:
            reduction = min(t["remaining_amount"], excess)
            t["remaining_amount"] -= reduction
            excess -= reduction
            if excess <= MONEY_EPSILON:
                break

    return detail["total"], tranches


def calculate_minimum_funds_required_v2(session=None, as_of=None):
    """New Model 2's own Minimum Funds Required total (docs/14) — Card 2 of
    the four-card UI flow (GET /models/5/minimum-funds-required). Uses
    required_amount_v2 for every vendor with an outstanding balance, Must
    Pay/Normal/Inactive/Commitment alike — reuses min_funds.py's own
    vendor+ledger loader (the has_outstanding_balance exclusion is identical
    for New Model 2, docs/14) rather than a second copy of that query.

    Duplicate-vendor handling: the ONE duplicate check in this codebase is
    ERP-code-based, at ingestion (backend/ingestion/load_excel.py) — a
    vendor_id here is by definition unique per ERP code, so there is
    nothing left to de-duplicate at this layer. (A same-name-different-
    signature check used to also live here; removed — Sarath's call, one
    check only, keyed on ERP code.)
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendors, ledger_by_vendor = _load_vendors_and_ledger(session)
        rows = []
        for vendor in vendors:
            amount, rule = required_amount_v2(vendor, ledger_by_vendor[vendor.id], as_of=as_of)
            rows.append(
                {
                    "vendor_id": vendor.id,
                    "erp_code": vendor.erp_code,
                    "vendor_name": vendor.vendor_name,
                    "category": vendor.category,
                    "required_amount": amount,
                    "rule": rule,
                }
            )
        breakdown = pd.DataFrame(
            rows,
            columns=["vendor_id", "erp_code", "vendor_name", "category", "required_amount", "rule"],
        )
        total = float(breakdown["required_amount"].sum()) if not breakdown.empty else 0.0
        return {"total": total, "breakdown": breakdown}
    finally:
        if owns_session:
            session.close()
