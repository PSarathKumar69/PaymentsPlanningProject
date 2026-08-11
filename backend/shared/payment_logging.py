"""Payment logging — prerequisite for the regeneration engine (docs/06).

Regeneration needs a real, live "how much has actually been paid this
month" number per vendor — this is where that number gets written.
"""
from datetime import date

from sqlalchemy import func

from backend.db.models import MonthlyLedger, Payment, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.shared.scoring import recompute_and_persist_scores
from backend.shared.constants import MONEY_EPSILON
from backend.shared.enums import PaymentStatus
from backend.weekly_planning.calendar_utils import week_for_date


def _current_cycle_month(session):
    """The month every plan_run in the active cycle shares — planner.py and
    regeneration.py both set plan_run.month to the DB's latest ingested
    ledger month, and backend/month_end/rollover.py uses this same value to
    detect whether a genuine new month has landed."""
    return session.query(func.max(MonthlyLedger.month)).scalar()


def _this_cycle_allocation(session, vendor_id, model_number=5):
    """What model `model_number`'s CURRENT plan allocates this vendor.
    Defaults to 5 (New Model 2) — the sole remaining model since Models
    1/2/3 and New Model 1 were retired; this was Model-1-only back when
    Model 1 was the flagship model log_payment() anchored payment_status
    to, but that anchor point has to move with whichever model is
    actually in use, or payment_status/regeneration-eligibility silently
    breaks the moment the old anchor model is deleted.

    Finds this vendor's PlanAllocation row on that model's latest plan_run
    (family-matched via a "model{n}%" prefix, so a "model{n}_regeneration"-
    style label still counts as the same family — plan_history.py's own
    convention), and returns vendor.override_amount if Finance has set one
    — the sticky, per-vendor, whole-month source of truth (docs/06 fix; no
    longer the per-row PlanAllocation.override_amount, which is now just a
    historical snapshot) — else the model's own allocated_amount. No
    plan_run at all for that model (or no allocation row for this vendor
    within it, e.g. no assigned_week) -> 0.0, same "nothing to be in full
    against yet" semantics as before, UNLESS Finance has set an override
    anyway (a sticky override applies regardless of whether a plan_run/
    allocation row exists for this vendor).

    Deliberately NOT scoped to `_current_cycle_month()` (the ledger-
    ingestion anchor): New Model 2's own "cycle" is Finance's picked
    planning month, stamped directly onto its own plan_run.month, which can
    genuinely differ from the ledger's latest ingested month (docs/14) —
    the single overall-latest plan_run for the model's family is always the
    right one to read, with no separate month filter needed.
    """
    vendor = session.query(Vendor).filter_by(id=vendor_id).one()
    if vendor.override_amount is not None:
        return float(vendor.override_amount)

    prefix = f"model{model_number}"
    latest_run = (
        session.query(PlanRun)
        .filter(PlanRun.model_used.like(f"{prefix}%"))
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )

    if latest_run is None:
        return 0.0

    allocation = (
        session.query(PlanAllocation)
        .filter(PlanAllocation.plan_run_id == latest_run.id, PlanAllocation.vendor_id == vendor_id)
        .first()
    )
    if allocation is None:
        return 0.0
    return float(allocation.allocated_amount)


def finalize_plan(session, model_number=5):
    """"Finalize Plan" — snapshots every vendor's CURRENT effective amount
    for model `model_number` (Finance's override if set, else that model's
    latest plan_run allocation — same precedence _this_cycle_allocation()
    already uses) into Vendor.finalized_budget_amount, the Vendor Payment
    Table's "Budget" column (see plan_history.py::latest_budget_for_vendor).

    Deliberately a snapshot, not a live figure: Finance can generate plans
    and tweak overrides repeatedly across a day/week/month, paying vendors
    as they go, and the Budget column only moves when this is explicitly
    called again — never silently drifts underneath an in-progress payment
    round. Resets to None at month-end rollover, same as override_amount.

    Perf fix (prod bug: Finalize was taking 60+ seconds for 450 vendors):
    calling _this_cycle_allocation() per vendor re-ran its OWN "find the
    latest plan_run for this model" query every single time, even though
    that query's answer is identical for every vendor in this loop — same
    for the per-vendor PlanAllocation lookup, one query each instead of one
    query for all of them. That's ~3 DB round trips × 450 vendors when it
    should be ~2 total. Same exact precedence as _this_cycle_allocation()
    (override_amount wins; else this one latest plan_run's allocation; else
    0.0 if there's no plan_run or no allocation row for this vendor) —
    just computed via two queries up front instead of inside the loop.
    _this_cycle_allocation() itself is untouched (still used elsewhere,
    e.g. the single-vendor payment-status path below) — this only changes
    how finalize_plan() computes its own snapshot.
    """
    vendors = session.query(Vendor).order_by(Vendor.id).all()

    prefix = f"model{model_number}"
    latest_run = (
        session.query(PlanRun)
        .filter(PlanRun.model_used.like(f"{prefix}%"))
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )
    allocation_by_vendor = {}
    if latest_run is not None:
        for allocation in (
            session.query(PlanAllocation)
            .filter(PlanAllocation.plan_run_id == latest_run.id)
            .order_by(PlanAllocation.id)
        ):
            allocation_by_vendor.setdefault(allocation.vendor_id, float(allocation.allocated_amount))

    for vendor in vendors:
        if vendor.override_amount is not None:
            vendor.finalized_budget_amount = float(vendor.override_amount)
        else:
            vendor.finalized_budget_amount = allocation_by_vendor.get(vendor.id, 0.0)
    return len(vendors)


def week_actual_paid(session, vendor_id):
    """{week_str: total_paid} for a vendor — a live aggregation of the real
    `payments` table, grouped by week. Always computed fresh, never stored
    redundantly (docs/06's regeneration engine already has this convention
    for paid_so_far_this_month; the planned split — week_distribution_plan —
    is the only thing that gets persisted).

    DEFAULT — confirm with Sarath: "this cycle" is scoped to
    payment_date >= _current_cycle_month(session) — the same ledger-month
    anchor _this_cycle_allocation() uses. There's nothing more precise to
    filter on: the month-end cycle reset (commit_upload()'s own new-month
    check, backend/month_end/rollover.py::_reset_vendor_cycle_state()) never
    purges/archives old Payment rows, and
    Payment carries no explicit cycle/plan_run link of its own. In a live
    system payment_date (the server's real current date, see log_payment)
    and the ledger's newly-ingested cycle month are the same real month, so
    this is equivalent to "this month's payments"; in this project's current
    dummy data (a historical sheet frozen months before today's real date)
    it instead resolves to "every payment logged so far" — still correct,
    since nothing predates the sheet's ingestion.
    """
    cycle_month = _current_cycle_month(session)
    rows = (
        session.query(Payment.week, func.sum(Payment.amount))
        .filter(Payment.vendor_id == vendor_id, Payment.payment_date >= cycle_month)
        .group_by(Payment.week)
        .all()
    )
    return {str(week): float(total) for week, total in rows if week is not None}


def _status_for(paid_so_far, cycle_allocation, opening_balance):
    if paid_so_far <= MONEY_EPSILON:
        return PaymentStatus.NOT_PAID
    # docs/06 fix: live outstanding balance hitting zero means PAID_IN_FULL
    # regardless of cycle_allocation — takes priority over the "no plan
    # allocation yet" branch below, since a vendor can clear their whole
    # balance before any plan_run exists this cycle.
    if paid_so_far >= opening_balance - MONEY_EPSILON:
        return PaymentStatus.PAID_IN_FULL
    # DEFAULT — confirm with Sarath: a vendor with no plan allocation at all
    # this cycle (cycle_allocation == 0, ~15 real vendors today) has nothing
    # to be "in full" against — any payment on them caps out at PARTIAL
    # rather than trivially satisfying paid_so_far >= 0 - MONEY_EPSILON.
    if cycle_allocation <= MONEY_EPSILON:
        return PaymentStatus.PARTIAL
    if paid_so_far >= cycle_allocation - MONEY_EPSILON:
        return PaymentStatus.PAID_IN_FULL
    return PaymentStatus.PARTIAL


def log_payment(vendor_id, amount, today=None, session=None, note=None):
    """Records one payment, updates the vendor's running paid-so-far total
    and payment_status.

    note: optional free-text Finance can attach (vendor detail modal's Pay
    flow) — purely descriptive (Payment.note), never read by any model or
    ledger-integrity check.

    Finance no longer supplies payment_date/week — both are determined here:
    payment_date is the real current date at the moment of logging (never
    client-supplied, never backdated), and week is derived from that same
    date via week_for_date(). `today` overrides "the real current date" only
    for test determinism (defaults to date.today()); it is not a caller-
    facing concept in the API.

    Two different "owed" concepts are in play here, deliberately not the
    same number: the overpayment guard below is a hard ledger-integrity cap
    against the vendor's real outstanding balance (opening_balance) — you
    can never pay someone more than they actually owe, full stop. The
    payment_status classification (NOT_PAID/PARTIAL/PAID_IN_FULL) is
    against this cycle's plan allocation instead (see
    _this_cycle_allocation) — confirmed by docs/06, since "paid in full"
    means "done for this cycle's plan," not "owes nothing at all anymore" —
    EXCEPT when the vendor's live outstanding balance (opening_balance -
    paid_so_far_this_month) itself hits zero, in which case PAID_IN_FULL
    applies regardless of cycle_allocation (docs/06 fix: a vendor who clears
    their whole balance before any plan_run exists this cycle must not get
    stuck at PARTIAL forever — see _status_for).

    A logged payment also triggers an immediate, stored rescore: aging,
    required-amount, and outstanding-balance are all live-payment-aware now
    (backend/shared/aging.py, backend/shared/min_funds.py), so the payment
    must be reflected in every vendor's score/current_aging_bucket right
    away, not just next time someone happens to call score_vendors(). This
    is a full recompute_and_persist_scores() run across the WHOLE vendor
    set (not just this one vendor) — see that function's docstring for why:
    outstanding_score is a percentile rank across all vendors, so one
    vendor's payment can shift everyone else's rank too. Performance
    trade-off, flagged rather than silently optimized away or silently left
    slow: this reruns score_vendors() (aging + rank across all ~83 vendors)
    on every single payment, in the same transaction — noticeably more work
    per log_payment() call than before, though still well under what a
    human would notice for a dataset this size.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        outstanding = float(vendor.opening_balance)
        new_total = float(vendor.paid_so_far_this_month) + amount
        if new_total > outstanding + MONEY_EPSILON:
            raise ValueError(
                f"{vendor.erp_code}: payment would push paid-so-far ({new_total:.2f}) "
                f"above what's owed ({outstanding:.2f}) — ledger-integrity violation"
            )

        payment_date = today or date.today()
        week = week_for_date(payment_date)
        session.add(Payment(vendor_id=vendor_id, payment_date=payment_date, amount=amount, week=week, note=note))
        vendor.paid_so_far_this_month = new_total
        # Defaults to model_number=5 (New Model 2, the sole model) —
        # payment_status is always anchored to whichever model is actually
        # in use, not a specific tab Finance happens to be viewing.
        cycle_allocation = _this_cycle_allocation(session, vendor_id)
        vendor.payment_status = _status_for(new_total, cycle_allocation, outstanding)
        session.flush()  # paid_so_far_this_month must be visible to the rescore query below
        recompute_and_persist_scores(session)

        if owns_session:
            session.commit()
        return vendor.payment_status
    finally:
        if owns_session:
            session.close()
