"""Month-end rollover (docs/06-weekly-planning-regeneration.md's "Month-end
rollover" section).

Re-ingests the Finance-updated Excel via the existing load() (never
duplicated here), then — only if that re-ingestion actually brought in a
genuinely new month — resets every vendor's monthly payment state so the
new cycle starts fresh, per Vendor.payment_status's own docstring
(backend/db/models.py): "Both reset at month start; not historized."

load() is also how Finance re-uploads a same-month correction (e.g. a typo
fix mid-month) — that must never wipe real payment history logged so far.
So the reset only fires when the sheet's latest month has strictly
advanced; otherwise this raises, but the re-ingested correction is still
saved (see the guard-refusal handling below) — only the reset is skipped.

Atomicity: load() now accepts this call's own session (see load_excel.py),
so re-ingestion and the payment-cycle reset share ONE transaction. A
genuine failure during the reset loop — after the new-month guard has
already confirmed a real new month — rolls back BOTH the reset attempt and
this call's re-ingestion together. Without that, the new month's ledger
data would already be committed by the time the reset failed, and a retry
would immediately hit the "not strictly newer" guard below (since the
ledger already looks rolled over), with no way to force the reset through
again short of manual DB surgery. See
test_rollover_reset_failure_rolls_back_ingestion_too for proof.

The one case that must NOT be rolled back this way: the "not strictly
newer" guard firing on an ordinary same-month correction. That's an
expected refusal, not a bug — Finance's re-ingested correction must still
be saved even though the reset itself is skipped — so that path explicitly
commits before raising instead of going through the rollback branch below.

Left untouched (already handled elsewhere, or out of scope per the prompt):
assigned_week/category/commitment_months (Finance-owned, already carried
forward by load()) and plan_runs/plan_allocations history (persists across
months per docs/12-database.md).
"""
from sqlalchemy import func

from backend.db.models import MonthlyLedger, Vendor
from backend.db.session import SessionLocal
from backend.ingestion.load_excel import EXCEL_PATH, load
from backend.shared.enums import PaymentStatus


def rollover_month(excel_path=EXCEL_PATH, session=None):
    """Re-ingests excel_path and, only on a genuine new month, resets every
    vendor's payment_status to NOT_PAID and paid_so_far_this_month to 0.

    Raises ValueError if the re-ingested sheet's latest month isn't
    strictly after the DB's latest month before this call —
    # DEFAULT — confirm with Sarath: refusing loudly, rather than silently
    # no-op-with-warning, so a rollover attempt that didn't actually land a
    # new month is never mistaken for having worked. The re-ingested data
    # itself (an ordinary same-month correction) is still saved in this
    # case — only the payment-cycle reset is skipped (see module docstring).

    Returns {"pre_month", "post_month", "vendors_reset", "load_report"}.
    """
    owns_session = session is None
    session = session or SessionLocal()
    guard_refused = False  # distinguishes an expected same-month refusal (commit) from a real failure (rollback)
    try:
        pre_month = session.query(func.max(MonthlyLedger.month)).scalar()

        load_report = load(excel_path=excel_path, session=session)  # same transaction now — no separate commit

        post_month = session.query(func.max(MonthlyLedger.month)).scalar()

        if pre_month is not None and (post_month is None or post_month <= pre_month):
            guard_refused = True
            raise ValueError(
                f"rollover_month: re-ingested latest month ({post_month}) is not strictly after "
                f"the DB's latest month before this call ({pre_month}) — refusing to reset payment "
                "state. This looks like a same-month correction re-upload, not a genuine new month; "
                "the re-ingested data itself has still been saved."
            )

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

        if owns_session:
            session.commit()

        return {
            "pre_month": pre_month,
            "post_month": post_month,
            "vendors_reset": len(vendors),
            "load_report": load_report,
        }
    except Exception:
        if owns_session:
            if guard_refused:
                session.commit()  # save the re-ingested correction; only the reset was skipped
            else:
                session.rollback()  # genuine failure — undo the reset attempt AND this call's re-ingestion together
        raise
    finally:
        if owns_session:
            session.close()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = rollover_month()
    logging.getLogger("rollover").info(
        "Rolled over %s -> %s, %d vendors reset", result["pre_month"], result["post_month"], result["vendors_reset"]
    )
