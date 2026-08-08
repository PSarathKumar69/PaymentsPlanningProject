"""Month-end payment-cycle reset (docs/06-weekly-planning-regeneration.md's
"Month-end rollover" section).

Originally a standalone "Month-end rollover" action (its own button,
router, `rollover_month()` wrapping `load()` itself) — merged into the
regular upload flow instead (Sarath: Finance should never have to remember
which button to click). `backend/ingestion/upload.py::commit_upload()` is
now the only caller: it re-ingests via `load()` as normal, compares the
sheet's latest month to the DB's latest month from before that call, and —
only if the month genuinely advanced — calls `_reset_vendor_cycle_state()`
below in the same transaction as the ingestion. A same-month correction
re-upload (the ordinary, routine case) simply skips the reset; there is no
separate refusal/error path any more (that framing belonged to the old
standalone button, where a non-advancing month was a mistaken user action —
once every upload takes this path, most re-uploads during a month ARE
corrections).

`_reset_vendor_cycle_state()` resets exactly what Vendor.payment_status's
own docstring (backend/db/models.py) describes: "Both reset at month
start; not historized" — payment_status, paid_so_far_this_month,
reconsider, override_amount, week_distribution_plan,
finalized_budget_amount. Left alone (already handled elsewhere, or out of
scope): assigned_week/category/commitment_months (Finance-owned, already
carried forward by `load()`) and plan_runs/plan_allocations history
(persists across months per docs/12-database.md).

Atomicity is `commit_upload()`'s responsibility now, same guarantee the
old `rollover_month()` had: this function never commits its own
transaction, so a genuine failure here rolls back together with whatever
else that call's session was about to write.
"""
from backend.db.models import Vendor
from backend.shared.enums import PaymentStatus


def _reset_vendor_cycle_state(session):
    """Resets every vendor's whole-month, sticky, not-historized
    payment-cycle state for a genuine new cycle. Does not commit — caller
    owns the transaction. Returns the number of vendors reset."""
    vendors = session.query(Vendor).all()
    for vendor in vendors:
        vendor.payment_status = PaymentStatus.NOT_PAID
        vendor.paid_so_far_this_month = 0
        # DEFAULT — confirm with Sarath: reconsider is reset to None here
        # too, for display/audit cleanliness — but this has no functional
        # effect on regeneration correctness either way, since
        # regenerate()'s eligibility check reads a fresh
        # reconsider_decisions dict passed in per call, never this
        # persisted column (regeneration.py::determine_eligibility). A
        # stale value left in the column would simply be inert.
        vendor.reconsider = None
        # Both are sticky, per-vendor, WHOLE-MONTH decisions (docs/06's
        # plan-history feature) — a new cycle must start clean, same as
        # payment_status/paid_so_far_this_month above. Neither is
        # historized: the frozen per-plan_run snapshots on
        # PlanAllocation.override_amount/week_distribution_plan already
        # preserve what a past cycle's plans looked like.
        vendor.override_amount = None
        vendor.week_distribution_plan = None
        # Same sticky, whole-month, not-historized reset — a new cycle's
        # Vendor Payment Table must start blank until Finance finalizes
        # again, not silently carry last month's frozen Budget forward.
        vendor.finalized_budget_amount = None
    return len(vendors)
