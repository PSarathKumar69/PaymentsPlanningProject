"""Plan-run "history" and "model family" helpers.

A model's own plan_runs carry two different model_used labels across the
initial-generation vs. regeneration code paths (e.g. "model2" from
model2.py's generate-plan-and-weekly-view vs. "model2_regeneration" from
regeneration.py) — everywhere in this module treats those as ONE family,
matched with a "model{n}%" prefix, the same convention
backend/api/routers/model1.py's _last_model1_allocated_amount() and
backend/shared/payment_logging.py's _this_cycle_allocation() already use for
Model 1's single "model1" label (Model 1 has no regeneration variant, so an
exact match and a "model1%" prefix match are equivalent for it).

Used by:
  - backend/api/routers/plan_allocations.py — the override-edit guard
    (only the latest plan_run in a model's family is editable).
  - backend/api/routers/model1.py / model2.py / model3.py — each model's
    GET /models/{n}/plan-runs history endpoint.
"""
import re
from collections import defaultdict

from backend.db.models import PlanAllocation, PlanRun, Vendor
from backend.shared.payment_logging import _current_cycle_month

_FAMILY_RE = re.compile(r"(model\d+)")


def model_family_prefix(model_used: str) -> str:
    """"model1" -> "model1", "model2_regeneration" -> "model2", etc."""
    match = _FAMILY_RE.match(model_used)
    if not match:
        raise ValueError(f"Cannot determine model family for model_used={model_used!r}")
    return match.group(1)


def plan_runs_for_model(session, model_number: int, cycle_month=None):
    """Every PlanRun for this model family + the current cycle month
    (_current_cycle_month, same anchor payment_logging.py/rollover.py use),
    oldest first (index 0 = "Plan 1") — the GET history endpoint's ordering
    contract.

    cycle_month: additive override (docs/14) — every existing caller omits
    this and gets the exact behavior above, unchanged. New Model 2's own
    "cycle" is Finance's own picked planning month (backend/api/routers/
    new_model_2.py), never the ledger-ingestion-anchored
    _current_cycle_month() every other model still uses — passed in
    explicitly by that router's own GET /models/5/plan-runs instead of
    branching on model_number here.
    """
    cycle_month = cycle_month if cycle_month is not None else _current_cycle_month(session)
    prefix = f"model{model_number}"
    return (
        session.query(PlanRun)
        .filter(PlanRun.month == cycle_month, PlanRun.model_used.like(f"{prefix}%"))
        .order_by(PlanRun.created_at.asc(), PlanRun.id.asc())
        .all()
    )


def is_latest_plan_run(session, plan_run: PlanRun, cycle_month=None) -> bool:
    """Whether plan_run is the latest plan_run in its OWN model family +
    current cycle month — same order_by(created_at desc, id desc).first()
    "latest" pattern used everywhere else (payment_logging.py, model1.py).
    Used by the override-edit guard: editing an override is only allowed on
    the current latest plan_run for that allocation's model scope.

    cycle_month: additive override (docs/14), same convention as
    plan_runs_for_model() above — every existing caller omits this and gets
    the exact behavior below, unchanged. backend/api/routers/
    plan_allocations.py passes plan_run.month itself for a New Model 2
    allocation (whose "cycle" is Finance's own picked planning month, not
    the ledger-anchored one) instead of special-casing model_number here.
    """
    prefix = model_family_prefix(plan_run.model_used)
    cycle_month = cycle_month if cycle_month is not None else _current_cycle_month(session)
    latest = (
        session.query(PlanRun)
        .filter(PlanRun.month == cycle_month, PlanRun.model_used.like(f"{prefix}%"))
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )
    return latest is not None and latest.id == plan_run.id


def _latest_allocation_any_model(session, vendor_id):
    """The single shared query behind both latest_budget_for_vendor() and
    latest_priority_for_vendor(): among every plan_run that actually has an
    allocation row for this vendor, the one with the latest created_at.
    Returns the PlanAllocation row, or None if this vendor has never
    appeared in any plan_run.

    Deliberately per-vendor-aware, not "the single most-recently-created
    plan_run overall regardless of vendor" — a vendor can be absent from
    the globally-latest plan_run (e.g. override-excluded, or already
    paid_in_full and excluded from a regeneration), in which case the
    correct answer is an older run that did include them, not None/zero.

    Deliberately NOT scoped to `_current_cycle_month()` (the ledger-
    ingestion anchor): New Model 2's own plan_run.month is Finance's picked
    planning month (docs/14), which can genuinely differ from the ledger's
    latest ingested month — the single overall-latest allocation for this
    vendor is always the right one to read, no separate month filter
    needed (same reasoning as payment_logging.py::_this_cycle_allocation).
    """
    latest = (
        session.query(PlanAllocation)
        .join(PlanRun, PlanAllocation.plan_run_id == PlanRun.id)
        .filter(PlanAllocation.vendor_id == vendor_id)
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )
    return latest


def latest_budget_for_vendor(session, vendor_id):
    """"Budget" for the Vendor Payment Table (actual-payment tracking view,
    distinct from the per-model planning table).

    RESOLVED (was an open question): there is now an explicit "Finalize
    Plan" action (backend/shared/payment_logging.py::finalize_plan()) that
    snapshots Model 1's then-current effective amount (override if set,
    else Model 1's latest plan_run allocation) into
    Vendor.finalized_budget_amount. This just reads that stable snapshot —
    a live-following "whichever plan_run is newest" figure would have
    changed underneath Finance mid-payment-round every time they tweaked an
    override or regenerated, which is exactly what Finalize exists to
    prevent. 0.0 if Finance hasn't finalized yet this cycle.
    """
    vendor = session.query(Vendor).filter_by(id=vendor_id).one()
    return float(vendor.finalized_budget_amount) if vendor.finalized_budget_amount is not None else 0.0


def latest_priority_for_vendor(session, vendor_id):
    """within_week_order from the same "latest plan_run, any model" row
    latest_budget_for_vendor() reads — reused so the planning table's
    Priority column (Wn-Pm) reflects whichever model was actually used
    latest, not whichever tab happens to be active (the previous, tab-
    scoped behavior). No override involved: within_week_order is a
    code-computed execution order, never something Finance overrides
    directly. Returns None if this vendor has no allocation in any
    plan_run this cycle (same "nothing to show yet" semantics as
    latest_budget_for_vendor's 0.0).
    """
    allocation = _latest_allocation_any_model(session, vendor_id)
    return allocation.within_week_order if allocation is not None else None


def latest_week_distribution_for_vendor(session, vendor_id):
    """week_distribution_plan from the same "latest plan_run, any model" row
    latest_budget_for_vendor()/latest_priority_for_vendor() read.

    Confirmed real bug fix: Vendor.week_distribution_plan itself is only
    ever written by two manual Finance-edit actions (plan_allocations.py's
    override-rescale and its own dedicated week-distribution edit) — a
    vendor nobody has individually edited keeps it None forever, even
    though planner.py computes a correct per-cycle default (sticky vendor
    value if one exists, else {assigned_week: effective_amount}) onto
    every PlanAllocation row, every single generate. This reads THAT
    already-computed value instead of the near-always-empty Vendor field.
    None if this vendor has no allocation in any plan_run this cycle."""
    allocation = _latest_allocation_any_model(session, vendor_id)
    return allocation.week_distribution_plan if allocation is not None else None


def latest_week_distributions_for_vendors(session, vendor_ids):
    """Batched form of latest_week_distribution_for_vendor() above — ONE
    query for every id in `vendor_ids` instead of one query each. Perf fix
    (prod bug, confirmed 2026-08: finalized-plan-export taking 10+ seconds):
    the export was calling the per-vendor version once per finalized
    vendor whenever Vendor.week_distribution_plan itself was empty — which
    per that function's own docstring is the near-always case — so this
    ran for almost every vendor, each one a real Neon round trip.

    Same "latest plan_run, any model" precedence as the per-vendor
    version: among every plan_run with an allocation row for a given
    vendor, the one with the latest created_at wins (ORDER BY here, then
    first-occurrence-per-vendor-id wins in the loop below — equivalent to
    calling _latest_allocation_any_model() for each id, just in one round
    trip). Returns {vendor_id: week_distribution_plan} — a vendor_id with
    no allocation in any plan_run at all is simply absent from the dict,
    same as the per-vendor version returning None for that id."""
    if not vendor_ids:
        return {}
    rows = (
        session.query(PlanAllocation.vendor_id, PlanAllocation.week_distribution_plan)
        .join(PlanRun, PlanAllocation.plan_run_id == PlanRun.id)
        .filter(PlanAllocation.vendor_id.in_(vendor_ids))
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .all()
    )
    result = {}
    for vendor_id, week_distribution_plan in rows:
        result.setdefault(vendor_id, week_distribution_plan)  # first-seen per id, given the ORDER BY above, is the latest
    return result


def build_plan_run_history_response(session, model_number: int, cycle_month=None) -> dict:
    """Shape returned by GET /models/{n}/plan-runs (model1.py/model2.py/
    model3.py) — see those routers' docstrings for the endpoint contract.

    Deliberately does NOT include week_distribution_plan on a per-run
    allocation row anymore: distribution is no longer per-plan-run (this
    task) — it's one shared, ongoing value per vendor for the current
    cycle (Vendor.week_distribution_plan). The one clear place a frontend
    reads a vendor's CURRENT distribution from is this response's own
    top-level `vendor_week_distribution_plans` map, keyed by vendor_id (as a
    string, same JSON-object-key convention week_distribution_plan itself
    already uses) — built from every vendor that appears anywhere in the
    plan_runs below, not just the latest one, so a frontend viewing older
    history still has a vendor name/id to look up distribution against.
    (The DB column PlanAllocation.week_distribution_plan still exists
    unchanged, for historical/audit purposes only — just not surfaced here.)

    Bug fix (this task): a vendor Finance has never manually edited a week
    for (Vendor.week_distribution_plan still None) now falls back to their
    LATEST plan_run's own row-level default (planner.py/regeneration.py
    already compute {assigned_week: allocated_amount} fresh every single
    regeneration — that was always correct, it just never reached this
    response before). Without this, the planning table's Distribution
    columns showed nothing at all until Finance manually touched a week,
    even though a plan had already been suggested. Deliberately still
    falls back live (not written back onto Vendor.week_distribution_plan
    anywhere) so it keeps tracking whatever the CURRENT assigned_week/
    amount is across further regenerations, right up until Finance's own
    PATCH /plan-allocations/{id}/week-distribution actually sets the sticky
    field — at which point that frozen value takes over, same as before.
    """
    plan_runs = plan_runs_for_model(session, model_number, cycle_month=cycle_month)

    # Perf fix (prod bug, confirmed 2026-08: Planning page loading slowly) —
    # this used to query PlanAllocation once PER plan_run inside the loop
    # below, one real Neon round trip each. New Model 2 creates a fresh
    # plan_run row on every single Generate/Regenerate click (no dedicated
    # regeneration endpoint), so a cycle with months of live usage can have
    # dozens of plan_runs by the time Finance opens the Planning page — this
    # batches it into ONE query for every plan_run in the family, grouped by
    # plan_run_id in Python. The ORDER BY here already sorts by plan_run_id
    # first, so grouping preserves the same per-run assigned_week/
    # within_week_order ordering the old per-run query produced.
    plan_run_ids = [plan_run.id for plan_run in plan_runs]
    allocations_by_run: dict[int, list] = defaultdict(list)
    if plan_run_ids:
        all_allocations = (
            session.query(PlanAllocation)
            .filter(PlanAllocation.plan_run_id.in_(plan_run_ids))
            .order_by(
                PlanAllocation.plan_run_id,
                PlanAllocation.assigned_week,
                PlanAllocation.within_week_order,
            )
            .all()
        )
        for allocation in all_allocations:
            allocations_by_run[allocation.plan_run_id].append(allocation)

    vendor_ids: set[int] = set()
    latest_snapshot_by_vendor: dict[int, dict] = {}
    plan_run_dicts = []
    for plan_run in plan_runs:
        allocations = allocations_by_run.get(plan_run.id, [])
        allocation_dicts = []
        for allocation in allocations:
            vendor_ids.add(allocation.vendor_id)
            # plan_runs is oldest-first, so this is simply overwritten as we
            # go — after the loop, only the LATEST plan_run's snapshot per
            # vendor survives.
            latest_snapshot_by_vendor[allocation.vendor_id] = allocation.week_distribution_plan
            allocation_dicts.append(
                {
                    "plan_allocation_id": allocation.id,
                    "vendor_id": allocation.vendor_id,
                    "assigned_week": allocation.assigned_week,
                    "within_week_order": allocation.within_week_order,
                    "allocated_amount": float(allocation.allocated_amount),
                    "override_amount": (
                        float(allocation.override_amount) if allocation.override_amount is not None else None
                    ),
                    # Frozen denominator (this task's fix) — lets the
                    # frontend compute Suggested %/Override % for every
                    # historical plan_run, not just whichever one is still
                    # in the browser's live "rich" cache.
                    "required_amount_snapshot": (
                        float(allocation.required_amount_snapshot)
                        if allocation.required_amount_snapshot is not None
                        else None
                    ),
                }
            )
        plan_run_dicts.append(
            {
                "plan_run_id": plan_run.id,
                "created_at": plan_run.created_at,
                "month": plan_run.month,
                "model_used": plan_run.model_used,
                "funds_figure": float(plan_run.funds_figure) if plan_run.funds_figure is not None else None,
                "min_funds_required": (
                    float(plan_run.min_funds_required) if plan_run.min_funds_required is not None else None
                ),
                "leftover_remaining": (
                    float(plan_run.leftover_remaining) if plan_run.leftover_remaining is not None else None
                ),
                "allocations": allocation_dicts,
            }
        )

    vendor_week_distribution_plans: dict[str, dict[str, float] | None] = {}
    if vendor_ids:
        vendors = session.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all()
        vendor_week_distribution_plans = {
            str(v.id): (
                v.week_distribution_plan
                if v.week_distribution_plan is not None
                else latest_snapshot_by_vendor.get(v.id)
            )
            for v in vendors
        }

    return {"plan_runs": plan_run_dicts, "vendor_week_distribution_plans": vendor_week_distribution_plans}
