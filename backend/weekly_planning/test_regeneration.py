"""Tests against the real ingested database (backend/db/app.db) — no synthetic data,
except where explicitly constructed (noted per-test): the DB has no live
partial-payment/Reconsider history yet, so tests log payments and pass
Reconsider decisions themselves to build the "as of regeneration moment"
state docs/06 describes. Real: vendor identities, opening balances,
assigned_week tags, category. Constructed: the payment history and
Reconsider choice for this test run — rolled back after, never persisted
to app.db.
"""
import uuid
from datetime import date, datetime

import pandas as pd
import pytest

from backend.db.models import MonthlyLedger, PlanAllocation, PlanRun, Vendor
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import generate_plan as model1_generate_plan
from backend.models.model2_min_funds_advisory.advisor import generate_plan
from backend.models.model3_priority_bucket.bucketer import generate_plan as bucketer_generate_plan
from backend.shared.constants import MODEL_1_PLAN_RUN_LABEL
from backend.shared.enums import AllocationStatus, PaymentStatus, VendorCategory
from backend.shared.min_funds import required_amount
from backend.shared.payment_logging import _this_cycle_allocation, log_payment
from backend.weekly_planning.regeneration import determine_eligibility, regenerate

V00400_ERP = "V00400"  # real: assigned_week=2, the exact vendor docs/06 names as its test case


def _unallocated_vendor(session, min_balance=0):
    """Constructs a fresh, synthetic Vendor row — guaranteed zero
    PlanAllocation history by construction, not by luck. Used to be a query
    for a real vendor that *happened* to have zero PlanAllocation rows ever;
    after months of live Generate Plan/Regenerate usage, every one of the 83
    real vendors now has at least one, so that query permanently returns
    nothing. Same pattern as backend/shared/test_payment_logging.py's own
    _unallocated_vendor() — not imported from there, since each test file
    owns its own small helpers here (see _give_vendor_a_model1_cycle_allocation
    below, which is likewise file-local). Rolled back by the caller's own
    `finally: session.rollback()`.
    """
    vendor = Vendor(
        erp_code=f"TEST-{uuid.uuid4().hex[:10]}",
        entity="Test Entity",
        vendor_name="Synthetic Test Vendor",
        opening_balance=float(min_balance) + 5_000_000.0,
    )
    session.add(vendor)
    session.flush()
    return vendor


def _give_vendor_a_model1_cycle_allocation(session, vendor, amount, created_at=datetime(2099, 1, 1)):
    """payment_status is now scoped to Model 1's latest plan_run alone
    (backend/shared/payment_logging.py) — a test that calls log_payment()
    and expects a specific PARTIAL/PAID_IN_FULL outcome needs a KNOWN Model
    1 allocation to measure against, not whatever this vendor's real,
    already-accumulated Model 1 plan_run history happens to allocate them
    (which drifts as this dummy DB gets exercised by live testing).
    created_at defaults far in the future so this is always Model 1's
    latest plan_run for this vendor this cycle, regardless of what's
    already there — pass an explicit (earlier) created_at for a test that
    itself calls regenerate() afterward, since that uses real datetime.now()
    and needs to end up later than this constructed row, not earlier.
    """
    cycle_month = session.query(MonthlyLedger.month).order_by(MonthlyLedger.month.desc()).first()[0]
    plan_run = PlanRun(created_at=created_at, month=cycle_month, model_used=MODEL_1_PLAN_RUN_LABEL, funds_figure=amount)
    session.add(plan_run)
    session.flush()
    session.add(
        PlanAllocation(plan_run_id=plan_run.id, vendor_id=vendor.id, assigned_week=1, within_week_order=1, allocated_amount=amount)
    )
    session.flush()


def test_v00400_pulled_forward_scenario_end_to_end():
    """Constructed state: V00400 (real vendor, real assigned_week=2, real
    opening balance) gets a partial payment logged and Reconsider=Yes set,
    then a W4 regeneration is triggered — mirroring docs/06's own real
    example (V00400 partially paid in W2, topped up in W4 via Reconsider=Yes).
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code=V00400_ERP).one()
        owed = float(vendor.opening_balance)

        partial_amount = 6_800_000.0  # matches the real W2 figure from the sheet
        status = log_payment(vendor.id, partial_amount, date(2026, 5, 8), session=session)
        assert status == PaymentStatus.PARTIAL

        result = regenerate(
            generate_plan_fn=generate_plan,
            available_funds=200_000_000,
            current_week=4,
            reconsider_decisions={vendor.id: True},
            session=session,
            model_used="model2_regeneration",
        )

        assert vendor.id in result["pulled_forward"]
        assert vendor.id not in result["excluded"]
        assert vendor.assigned_week == 2  # never reassigned — Finance-owned data

        row = result["allocations"][result["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["display_week"] == 4  # top-up shown under the current week, not the original W2 tag

        # Ledger integrity: top-up + prior partial payment must never exceed what's owed.
        assert partial_amount + row["allocated_amount"] <= owed + 1

        persisted = session.query(PlanAllocation).filter_by(plan_run_id=result["plan_run_id"], vendor_id=vendor.id).one()
        assert persisted.assigned_week == 4
        assert float(persisted.allocated_amount) == pytest.approx(row["allocated_amount"], abs=1e-3)
    finally:
        session.rollback()
        session.close()


def test_locked_vendor_gets_no_new_allocation_and_is_untouched():
    """Constructed: partial payment logged, Reconsider left unset (No)."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V01542").one()  # real: assigned_week=1
        owed = float(vendor.opening_balance)
        _give_vendor_a_model1_cycle_allocation(session, vendor, owed)  # cycle allocation == full balance, so 20% is genuinely partial
        log_payment(vendor.id, owed * 0.2, date(2026, 5, 3), session=session)
        paid_before = float(vendor.paid_so_far_this_month)
        status_before = vendor.payment_status

        result = regenerate(
            generate_plan_fn=generate_plan,
            available_funds=200_000_000,
            current_week=3,
            reconsider_decisions={},  # no decision given -> locked, not pulled forward
            session=session,
        )

        assert result["excluded"][vendor.id] == "locked"
        assert vendor.id not in result["allocations"]["vendor_id"].values
        # "Do not touch again" — regeneration must not alter their payment state.
        assert float(vendor.paid_so_far_this_month) == paid_before
        assert vendor.payment_status == status_before
        assert session.query(PlanAllocation).filter_by(vendor_id=vendor.id, plan_run_id=result["plan_run_id"]).count() == 0
    finally:
        session.rollback()
        session.close()


def test_paid_in_full_vendor_excluded_permanently():
    """Constructed: vendor paid their full cycle allocation, deliberately
    smaller than their real outstanding balance so this exercises the
    payment_status/cycle-allocation eligibility branch specifically, not the
    separate zero-outstanding-balance exclusion (docs/06 fix) which now
    excludes a vendor upstream, before determine_eligibility ever runs —
    see test_zero_outstanding_vendor_excluded_even_if_status_label_is_stale
    for that scenario.
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00080").one()  # real: assigned_week=1
        owed = float(vendor.opening_balance)
        cycle_allocation = owed * 0.3  # comfortably less than owed -> real outstanding balance remains after payment
        _give_vendor_a_model1_cycle_allocation(session, vendor, cycle_allocation)
        status = log_payment(vendor.id, cycle_allocation, date(2026, 5, 2), session=session)
        assert status == PaymentStatus.PAID_IN_FULL

        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)

        assert result["excluded"][vendor.id] == "paid_in_full_excluded"
        assert vendor.id not in result["allocations"]["vendor_id"].values
    finally:
        session.rollback()
        session.close()


def test_not_paid_vendor_with_elapsed_week_is_normal_not_pulled_forward():
    """docs/06 fix: "pulled_forward" means specifically a top-up on an
    existing partial payment — a NOT_PAID vendor whose assigned_week has
    simply elapsed (the common case, Finance rarely regenerates on day 1)
    must be labeled "normal" and keep displaying under its own assigned_week,
    not get relabeled/reassigned to the current week like a genuine top-up.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        vendor.assigned_week = 1  # elapsed relative to current_week=3 below
        assert vendor.payment_status == PaymentStatus.NOT_PAID  # nothing paid yet
        session.flush()

        eligible, reason = determine_eligibility(vendor, current_week=3, reconsider_decisions={})
        assert eligible
        assert reason == "normal"

        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)
        assert vendor.id not in result["excluded"]
        assert vendor.id not in result["pulled_forward"]
        row = result["allocations"][result["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["display_week"] == 1  # own assigned_week, never reassigned to the current week
    finally:
        session.rollback()
        session.close()


def test_zero_outstanding_vendor_excluded_even_if_status_label_is_stale():
    """docs/06 fix: the zero-outstanding check takes priority over the
    payment_status branches entirely — even a vendor whose status label
    somehow still says NOT_PAID/PARTIAL (the exact bug this fix sidesteps,
    since _status_for only reruns inside log_payment()) must be excluded
    once their live outstanding balance is zero.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session)
        vendor.assigned_week = 2
        vendor.paid_so_far_this_month = float(vendor.opening_balance)  # zero outstanding
        vendor.payment_status = PaymentStatus.NOT_PAID  # deliberately stale/wrong label
        session.flush()

        eligible, reason = determine_eligibility(vendor, current_week=3, reconsider_decisions={})
        assert not eligible
        assert reason == "no_outstanding_balance"
    finally:
        session.rollback()
        session.close()


def test_partial_payment_reduces_re_suggested_amount_on_regeneration():
    """Bug fix: a payment logged mid-cycle must not be invisible to a later
    regeneration — required_amount() (which feeds allocated_amount/status)
    must reflect what the vendor still actually needs, not their full
    pre-payment figure, so they don't keep soaking up funds as if the
    payment never happened.
    """
    session = SessionLocal()
    try:
        vendor = (
            session.query(Vendor)
            .filter(
                Vendor.assigned_week.isnot(None),
                Vendor.payment_status != PaymentStatus.PAID_IN_FULL,
                Vendor.paid_so_far_this_month == 0,
                Vendor.opening_balance > 500_000,
            )
            .order_by(Vendor.id)
            .first()
        )
        assert vendor is not None
        ledger = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
        required_before, _ = required_amount(vendor, ledger)
        assert required_before > 0

        payment = required_before * 0.4  # partial, stays within the oldest bucket
        log_payment(vendor.id, payment, date(2026, 5, 10), session=session)

        result = regenerate(
            generate_plan_fn=model1_generate_plan,
            available_funds=200_000_000,  # generous — isolates required_amount from any funds-shortfall scaling
            current_week=vendor.assigned_week,
            # This partial payment makes them PARTIAL — under the revised
            # eligibility rule (determine_eligibility) that needs an explicit
            # Reconsider=Yes for THIS call regardless of week, unlike the old
            # "current week or later is automatic" rule this test originally
            # relied on.
            reconsider_decisions={vendor.id: True},
            session=session,
            model_used=MODEL_1_PLAN_RUN_LABEL,
        )

        row = result["allocations"][result["allocations"]["vendor_id"] == vendor.id]
        assert not row.empty
        required_after = row.iloc[0]["required_amount"]
        assert required_after == pytest.approx(required_before - payment, abs=1)
        assert required_after < required_before
    finally:
        session.rollback()
        session.close()


def test_excluding_paid_in_full_must_pay_vendor_before_computing_does_not_shrink_eligible_funds():
    """docs/06/04/05's confirmed fix: an excluded vendor must not consume
    part of the funds pool internally. Constructed: tag a real vendor
    MUST_PAY and PAID_IN_FULL, then compare the "before" (vendor still part
    of the model's computation) against the "after" (regenerate() properly
    excludes them via eligible_vendor_ids) to prove the fix actually
    changes the outcome, not just that it runs.
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()  # real: assigned_week=2
        vendor.category = VendorCategory.MUST_PAY
        vendor.payment_status = PaymentStatus.PAID_IN_FULL  # constructed: done for this cycle
        session.flush()

        available_funds = 50_000_000  # a real shortfall relative to the full 83-vendor need

        # "Before": call generate_plan across all vendors, as the old code did —
        # the excluded vendor's required_amount still eats into guaranteed_total.
        buggy_plan = bucketer_generate_plan(available_funds, session=session)
        buggy_p2_p4_total = buggy_plan["allocations"][buggy_plan["allocations"]["bucket"].isin(["P2", "P3", "P4"])][
            "allocated_amount"
        ].sum()

        # "After": regenerate() passes eligible_vendor_ids through, excluding
        # this vendor from the model's computation entirely.
        result = regenerate(
            generate_plan_fn=bucketer_generate_plan,
            available_funds=available_funds,
            current_week=3,
            session=session,
            model_used="model3_regeneration",
        )
        fixed_p2_p4_total = result["allocations"][result["allocations"]["bucket"].isin(["P2", "P3", "P4"])][
            "allocated_amount"
        ].sum()

        assert result["excluded"][vendor.id] == "paid_in_full_excluded"
        assert vendor.id not in result["allocations"]["vendor_id"].values
        assert fixed_p2_p4_total > buggy_p2_p4_total  # the fix measurably changes the outcome
    finally:
        session.rollback()
        session.close()


def test_future_week_not_paid_vendor_included_normally_no_reconsider_needed():
    """Real: V01524, assigned_week=5, still NOT_PAID (as ingested) — their
    week hasn't arrived yet at a W3 regeneration, no Reconsider decision needed."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V01524").one()
        assert vendor.payment_status == PaymentStatus.NOT_PAID  # real, unmodified

        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)

        assert vendor.id not in result["excluded"]
        row = result["allocations"][result["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["display_week"] == 5  # own assigned_week, not the current regeneration week
    finally:
        session.rollback()
        session.close()


def test_capped_vendor_reclaim_flows_to_underfunded_vendor_with_relabeled_status():
    """docs/06's confirmed fix: money _capped() claws back from a vendor
    already paid close to their real balance must not vanish — it flows to
    a genuinely underfunded vendor (real funds shortfall, not a cap), and
    both vendors' status label reflects the FINAL post-redistribution
    amount, never the model's original pre-redistribution label.

    A fake generate_plan_fn stands in for Model 2/3 here so the scenario is
    fully deterministic (a fixed "need" per vendor) rather than depending on
    the real ledger's exact aging-bucket amounts — the redistribution logic
    under test lives entirely in regeneration.py and is model-agnostic (it
    only ever calls generate_plan_fn(available_funds, session=, eligible_vendor_ids=)).
    """
    session = SessionLocal()
    try:
        vendor_a = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 500_000)
            .order_by(Vendor.id)
            .first()
        )
        vendor_b = (
            session.query(Vendor)
            .filter(
                Vendor.category == VendorCategory.NORMAL,
                Vendor.opening_balance > 2_000_000,
                Vendor.id != vendor_a.id,
            )
            .order_by(Vendor.id)
            .first()
        )
        assert vendor_a is not None and vendor_b is not None, "need 2 distinct real Normal vendors for this scenario"

        # Constructed: A is nearly paid off already (tiny headroom left
        # against its real balance), so the fresh allocation below gets
        # capped hard; B is untouched (full headroom) and stays genuinely
        # underfunded once the fake model's funds run out.
        vendor_a.payment_status = PaymentStatus.NOT_PAID
        vendor_a.paid_so_far_this_month = float(vendor_a.opening_balance) - 50_000.0
        vendor_a.assigned_week = 3
        vendor_b.payment_status = PaymentStatus.NOT_PAID
        vendor_b.paid_so_far_this_month = 0.0
        vendor_b.assigned_week = 3
        session.flush()

        a_id, b_id = vendor_a.id, vendor_b.id
        needs = {a_id: 1_000_000.0, b_id: 800_000.0}

        def fake_generate_plan(available_funds, session=None, eligible_vendor_ids=None, as_of=None):
            ids = eligible_vendor_ids if eligible_vendor_ids is not None else set(needs)
            funds_left = available_funds
            rows = []
            for vid in [a_id, b_id]:  # fixed priority order, same on every call
                if vid not in ids:
                    continue
                need = needs[vid]
                if funds_left <= 0:
                    allocated, status = 0.0, "zero"
                elif funds_left >= need:
                    allocated, status = need, "full"
                else:
                    allocated, status = funds_left, "partial"
                funds_left -= allocated
                rows.append(
                    {
                        "vendor_id": vid,
                        "category": VendorCategory.NORMAL.value,  # both stand-ins are Normal-tier for this scenario
                        "required_amount": need,
                        "outstanding_balance": need,
                        "allocated_amount": allocated,
                        "status": status,
                    }
                )
            return {"allocations": pd.DataFrame(rows)}

        # A's full 1,000,000 need + a 300,000 partial top-up towards B's 800,000 need.
        result = regenerate(
            generate_plan_fn=fake_generate_plan,
            available_funds=1_300_000,
            current_week=3,
            session=session,
            model_used="fake_test_model",
        )

        allocations = result["allocations"]
        row_a = allocations[allocations["vendor_id"] == a_id].iloc[0]
        row_b = allocations[allocations["vendor_id"] == b_id].iloc[0]

        # A: capped down to its real headroom (50,000) — relabeled to
        # "partial" rather than left showing the model's stale "full".
        assert row_a["allocated_amount"] == pytest.approx(50_000.0, abs=1)
        assert row_a["status"] == "partial"

        # B: the 950,000 reclaimed from A (1,000,000 - 50,000) tops up B's
        # own 300,000 partial allocation to 1,100,000 — enough to cross its
        # 800,000 need, so status is relabeled "full" to match.
        assert row_b["allocated_amount"] == pytest.approx(1_100_000.0, abs=1)
        assert row_b["status"] == "full"

        # Not all of the 950,000 reclaimed from A had somewhere to go: B's
        # own topup call is scoped to its 800,000 need regardless of how
        # much of the 950,000 budget it's given, so only 800,000 of it
        # actually landed (on top of B's existing 300,000) — the remaining
        # 150,000 has no other underfunded vendor to reach and must show up
        # as unallocated_surplus (docs/06 fix), not vanish.
        assert result["unallocated_surplus"] == pytest.approx(150_000.0, abs=1)
    finally:
        session.rollback()
        session.close()


# ---- Guaranteed-tier (P0/P1) headroom cap + unallocated_surplus (docs/06 fix) --


def test_guaranteed_vendor_headroom_capped_relabeled_partial_on_regeneration():
    """Reproduces the confirmed bug report using the real, wired-together
    model + regeneration engine: a Must Pay vendor's fixed required_amount
    (last month's payable — doesn't shrink as they get paid down this cycle)
    ends up above their real remaining headroom. The allocation is
    correctly capped at headroom (ledger integrity, CLAUDE.md rule 1), and
    the status label must show "partial", never silently stay "guaranteed"
    (rule 5's whole point).

    unallocated_surplus == 0.0 here, NOT (required - headroom): with the
    shared-level model fix in place, advisor.generate_plan() ITSELF already
    caps this vendor at headroom before regenerate() ever sees the row —
    there is no over-allocation event left for regenerate()'s own _capped()
    to reclaim (confirmed by inspection: full_plan's allocated_amount for
    this vendor already equals eligible's, so reclaimed_total's contribution
    from this vendor is 0). The "shortfall" here is money the model itself
    never handed out at all, not money that flowed in and got clawed back —
    a structurally different case from test_unallocated_surplus_fully_absorbed_
    by_underfunded_normal_vendor below, which uses a deliberately
    non-capping fake model to exercise regen's OWN reclaim/redistribution
    machinery (still necessary defense-in-depth for any generate_plan_fn
    that doesn't self-cap).
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.category = VendorCategory.MUST_PAY
        assert vendor.assigned_week, "need a real vendor with an assigned_week for this scenario"
        session.flush()

        ledger = session.query(MonthlyLedger).filter_by(vendor_id=vendor.id).order_by(MonthlyLedger.month).all()
        required, _ = required_amount(vendor, ledger)
        assert required > 100_000

        headroom = required * 0.5  # deliberately below required_amount
        vendor.paid_so_far_this_month = max(float(vendor.opening_balance) - headroom, 0.0)
        session.flush()

        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)

        row = result["allocations"][result["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["status"] == "partial"
        assert row["allocated_amount"] == pytest.approx(headroom, abs=1)
        assert row["allocated_amount"] < row["required_amount"] - 1

        assert result["unallocated_surplus"] == pytest.approx(0.0, abs=1)
    finally:
        session.rollback()
        session.close()


def test_guaranteed_vendor_with_ample_headroom_stays_guaranteed_on_regeneration():
    """The non-capped case: a Must Pay vendor with plenty of headroom left
    must still show status == "guaranteed" — confirms the _relabel()
    rewrite (comparing every row against required_amount directly, rather
    than hardcoding "GUARANTEED never changes") didn't regress the common,
    uncapped case.
    """
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).order_by(Vendor.id).first()
        vendor.category = VendorCategory.MUST_PAY
        assert vendor.assigned_week
        session.flush()

        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)

        row = result["allocations"][result["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert row["status"] == "guaranteed"
        assert row["allocated_amount"] == pytest.approx(row["required_amount"], abs=1)
    finally:
        session.rollback()
        session.close()


def test_unallocated_surplus_fully_absorbed_by_underfunded_normal_vendor():
    """A round where a headroom-capped guaranteed-tier vendor's reclaim IS
    fully consumed by a genuinely underfunded Normal vendor must report
    unallocated_surplus == 0.0, not just leave the redistribution's own
    total looking correct with no visibility into whether anything was
    actually left over.

    A fake generate_plan_fn stands in for Model 2/3 (same convention as
    test_capped_vendor_reclaim_flows_to_underfunded_vendor_with_relabeled_status)
    so both the guaranteed-tier cap and the Normal-tier shortfall are fully
    deterministic rather than depending on the real ledger's exact figures.
    """
    session = SessionLocal()
    try:
        vendor_g = (
            session.query(Vendor)
            .filter(Vendor.category == VendorCategory.NORMAL, Vendor.opening_balance > 2_000_000)
            .order_by(Vendor.id)
            .first()
        )
        vendor_n = (
            session.query(Vendor)
            .filter(
                Vendor.category == VendorCategory.NORMAL,
                Vendor.opening_balance > 2_000_000,
                Vendor.id != vendor_g.id,
            )
            .order_by(Vendor.id)
            .first()
        )
        assert vendor_g is not None and vendor_n is not None, "need 2 distinct real Normal vendors for this scenario"

        # G: real headroom of only 700,000 against a fake 1,000,000
        # guaranteed-tier required_amount, so regeneration's own _capped()
        # (not the fake) does the clawback — same as a real Must Pay vendor
        # whose fixed required_amount outran their real remaining balance.
        vendor_g.category = VendorCategory.MUST_PAY
        vendor_g.payment_status = PaymentStatus.NOT_PAID
        vendor_g.paid_so_far_this_month = float(vendor_g.opening_balance) - 700_000.0
        vendor_g.assigned_week = 3
        # N: generous real headroom — never capped, genuinely underfunded
        # only by the fake model's own funds shortfall.
        vendor_n.payment_status = PaymentStatus.NOT_PAID
        vendor_n.paid_so_far_this_month = 0.0
        vendor_n.assigned_week = 3
        session.flush()

        g_id, n_id = vendor_g.id, vendor_n.id

        def fake_generate_plan(available_funds, session=None, eligible_vendor_ids=None, as_of=None):
            ids = eligible_vendor_ids if eligible_vendor_ids is not None else {g_id, n_id}
            rows = []
            if g_id in ids:
                # Guaranteed-tier: funds-pool-independent, always its full
                # required_amount from the model's own perspective (real
                # models cap this at headroom themselves too, but this fake
                # deliberately doesn't, to isolate regen's own _capped()).
                rows.append(
                    {
                        "vendor_id": g_id,
                        "category": VendorCategory.MUST_PAY.value,
                        "required_amount": 1_000_000.0,
                        "outstanding_balance": 1_000_000.0,
                        "allocated_amount": 1_000_000.0,
                        "status": AllocationStatus.GUARANTEED.value,
                    }
                )
            if n_id in ids:
                # Normal-tier: funds-dependent, up to its own 800,000 need.
                # This fake's available_funds directly represents what's
                # left for Normal vendors (it doesn't itself subtract a
                # guaranteed_total the way the real models do).
                need = 800_000.0
                allocated = min(available_funds, need)
                status = (
                    AllocationStatus.FULL.value
                    if allocated >= need - 1
                    else AllocationStatus.ZERO.value
                    if allocated <= 1
                    else AllocationStatus.PARTIAL.value
                )
                rows.append(
                    {
                        "vendor_id": n_id,
                        "category": VendorCategory.NORMAL.value,
                        "required_amount": need,
                        "outstanding_balance": need,
                        "allocated_amount": allocated,
                        "status": status,
                    }
                )
            return {"allocations": pd.DataFrame(rows)}

        # N gets 300,000 of its 800,000 need in the full round (partial,
        # genuine shortfall) — G's 1,000,000 gets capped to 700,000 by
        # regen's own _capped(), reclaiming exactly 300,000, which Tier 2
        # then hands entirely to N (topping it up to 600,000 — still not
        # fully funded, but the WHOLE 300,000 reclaim was absorbed).
        result = regenerate(
            generate_plan_fn=fake_generate_plan,
            available_funds=300_000,
            current_week=3,
            session=session,
            model_used="fake_test_model",
        )

        allocations = result["allocations"]
        row_g = allocations[allocations["vendor_id"] == g_id].iloc[0]
        row_n = allocations[allocations["vendor_id"] == n_id].iloc[0]

        assert row_g["allocated_amount"] == pytest.approx(700_000.0, abs=1)
        assert row_g["status"] == "partial"  # capped guaranteed-tier vendor — never silently "guaranteed"

        assert row_n["allocated_amount"] == pytest.approx(600_000.0, abs=1)  # 300,000 own + 300,000 reclaimed topup

        assert result["unallocated_surplus"] == pytest.approx(0.0, abs=1)
    finally:
        session.rollback()
        session.close()


# ---- Model 1 wired into /regenerate (backend/api/routers/regeneration.py) --


def test_all_four_eligibility_outcomes_against_model1():
    """Confirms Model 1 is now a genuine regeneration participant, covering
    all four of docs/06's eligibility outcomes in one regeneration: a
    PAID_IN_FULL vendor excluded, a PARTIAL+Reconsider=Yes vendor pulled
    forward (assigned_week unchanged, display_week == current), a
    PARTIAL+Reconsider=No vendor locked out untouched, and a NOT_PAID
    vendor whose week hasn't arrived yet included normally.
    """
    session = SessionLocal()
    try:
        vendor_full = _unallocated_vendor(session, min_balance=500_000)
        vendor_yes = _unallocated_vendor(session, min_balance=500_000)
        vendor_no = _unallocated_vendor(session, min_balance=500_000)
        vendor_future = _unallocated_vendor(session, min_balance=500_000)
        current_week = 3

        # PAID_IN_FULL: paid in full against a known Model 1 allocation.
        vendor_full.assigned_week = 1
        _give_vendor_a_model1_cycle_allocation(session, vendor_full, 300_000.0)
        assert log_payment(vendor_full.id, 300_000.0, date(2026, 5, 2), session=session) == PaymentStatus.PAID_IN_FULL

        # PARTIAL, past week, Reconsider=Yes -> pulled forward.
        vendor_yes.assigned_week = 1
        _give_vendor_a_model1_cycle_allocation(session, vendor_yes, 1_000_000.0)
        assert log_payment(vendor_yes.id, 300_000.0, date(2026, 5, 3), session=session) == PaymentStatus.PARTIAL

        # PARTIAL, past week, Reconsider left unset -> locked.
        vendor_no.assigned_week = 2
        _give_vendor_a_model1_cycle_allocation(session, vendor_no, 1_000_000.0)
        assert log_payment(vendor_no.id, 400_000.0, date(2026, 5, 4), session=session) == PaymentStatus.PARTIAL

        # NOT_PAID, week hasn't arrived yet -> included normally, no Reconsider needed.
        vendor_future.assigned_week = 5
        assert vendor_future.payment_status == PaymentStatus.NOT_PAID
        session.flush()

        result = regenerate(
            generate_plan_fn=model1_generate_plan,
            available_funds=200_000_000,
            current_week=current_week,
            reconsider_decisions={vendor_yes.id: True},
            session=session,
            model_used=MODEL_1_PLAN_RUN_LABEL,
        )
        allocations = result["allocations"]

        assert result["excluded"][vendor_full.id] == "paid_in_full_excluded"
        assert vendor_full.id not in allocations["vendor_id"].values

        assert vendor_yes.id in result["pulled_forward"]
        assert vendor_yes.id not in result["excluded"]
        assert vendor_yes.assigned_week == 1  # never reassigned — Finance-owned data
        row_yes = allocations[allocations["vendor_id"] == vendor_yes.id].iloc[0]
        assert row_yes["display_week"] == current_week  # top-up shown under the current week

        assert result["excluded"][vendor_no.id] == "locked"
        assert vendor_no.id not in allocations["vendor_id"].values

        assert vendor_future.id not in result["excluded"]
        row_future = allocations[allocations["vendor_id"] == vendor_future.id].iloc[0]
        assert row_future["display_week"] == 5  # own assigned_week, not the current regeneration week
    finally:
        session.rollback()
        session.close()


def test_regenerated_model1_plan_uses_model_1_plan_run_label_not_a_derived_string():
    """Concrete before/after for the router's label fix: _this_cycle_allocation()
    (payment_logging.py) filters strictly on MODEL_1_PLAN_RUN_LABEL. A
    regenerated Model 1 plan tagged with a derived "model1_regeneration"
    string (the bug this fix removes) is invisible to it — payment status
    would keep reading a stale earlier "model1" plan_run instead of the
    fresh regeneration. Proven by calling regenerate() with each label
    directly and comparing what _this_cycle_allocation() sees afterward.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=500_000)
        vendor.assigned_week = 5  # future week relative to current_week=3 below -> eligible with no Reconsider needed
        session.flush()

        # An earlier, real Model 1 plan with a small, known allocation —
        # created_at captured as "right now" (unlike this helper's usual
        # far-future default), since regenerate() below stamps its own
        # plan_run with its own later real datetime.now() and needs to land
        # after this one, not before; a hardcoded past date risks colliding
        # with real plan_runs this same dummy DB already accumulated today
        # from other testing.
        _give_vendor_a_model1_cycle_allocation(session, vendor, 50_000.0, created_at=datetime.now())
        assert _this_cycle_allocation(session, vendor.id) == pytest.approx(50_000.0)

        # "Before" the fix: regenerate tagged with a derived string — a real
        # regeneration happens, but the label is invisible to
        # _this_cycle_allocation()'s filter, so payment status still reads
        # the stale earlier 50,000 plan_run, ignoring this regeneration entirely.
        regenerate(
            generate_plan_fn=model1_generate_plan,
            available_funds=200_000_000,
            current_week=3,
            session=session,
            model_used="model1_regeneration",  # the bug this fix removes
        )
        assert _this_cycle_allocation(session, vendor.id) == pytest.approx(50_000.0)  # still stale — the bug

        # "After" the fix: regenerate tagged with the shared constant — now
        # visible, payment status reflects the regenerated allocation.
        fixed_result = regenerate(
            generate_plan_fn=model1_generate_plan,
            available_funds=200_000_000,
            current_week=3,
            session=session,
            model_used=MODEL_1_PLAN_RUN_LABEL,  # the fix
        )
        fixed_row = fixed_result["allocations"][fixed_result["allocations"]["vendor_id"] == vendor.id].iloc[0]
        assert _this_cycle_allocation(session, vendor.id) == pytest.approx(float(fixed_row["allocated_amount"]), abs=1)
    finally:
        session.rollback()
        session.close()


# ---- week_distribution_plan default on regeneration (display-only) --------


def test_week_distribution_plan_defaults_on_regeneration():
    """A vendor with no distribution edit yet this cycle (Vendor.
    week_distribution_plan still None) gets the default: the full
    (re-)allocated amount under its own display_week (the current week for
    a pulled-forward top-up). See
    test_week_distribution_plan_carries_forward_across_regeneration below
    for the sticky-carry-forward case once a vendor's distribution HAS been
    set — docs/06 follow-up (plan-history feature) made that the new
    default going forward; this only covers the "nothing set yet" case.
    """
    session = SessionLocal()
    try:
        result = regenerate(
            generate_plan_fn=model1_generate_plan,
            available_funds=200_000_000,
            current_week=3,
            session=session,
            model_used=MODEL_1_PLAN_RUN_LABEL,
        )
        persisted = session.query(PlanAllocation).filter_by(plan_run_id=result["plan_run_id"]).limit(20).all()
        assert persisted, "need at least one persisted allocation from this regeneration"
        for allocation in persisted:
            assert allocation.week_distribution_plan == {
                str(allocation.assigned_week): pytest.approx(float(allocation.allocated_amount))
            }
    finally:
        session.rollback()
        session.close()


def test_week_distribution_plan_carries_forward_across_regeneration():
    """docs/06 follow-up (plan-history feature): once a vendor's
    distribution is set (Vendor.week_distribution_plan), a regeneration must
    stamp that SAME sticky value onto its new row too — never reset it, same
    real behavior change planner.py's own carry-forward already makes for
    the initial plan.
    """
    session = SessionLocal()
    try:
        vendor = _unallocated_vendor(session, min_balance=500_000)
        vendor.assigned_week = 3  # NOT_PAID by default -> always eligible (docs/06), deterministic
        custom_distribution = {"2": 4321.0}
        vendor.week_distribution_plan = custom_distribution
        session.flush()

        result = regenerate(generate_plan_fn=generate_plan, available_funds=200_000_000, current_week=3, session=session)

        assert vendor.id not in result["excluded"]
        persisted = (
            session.query(PlanAllocation).filter_by(plan_run_id=result["plan_run_id"], vendor_id=vendor.id).one()
        )
        assert persisted.week_distribution_plan == custom_distribution
    finally:
        session.rollback()
        session.close()
