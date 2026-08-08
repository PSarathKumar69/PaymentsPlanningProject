"""Router-level tests for New Model 2's planning-month plumbing and new
minimum-funds-required endpoints (docs/14-new-model-2.md Part B/D). Same
isolation convention as backend/api/test_api.py (fresh temp DB per test,
whole backend.* import graph re-bound) — duplicated here rather than
shared via conftest.py, matching this project's existing per-file-fixture
convention (see test_api.py's own docstring).

There was no pre-existing router-level test coverage for New Model 2 (or
New Model 1) at all before this task — nothing here replaces or risks an
existing test.
"""
import shutil
import sys

import os
import pytest
from fastapi.testclient import TestClient

from backend.ingestion.load_excel import EXCEL_PATH


def _fresh_app(tmp_path):
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]
    from backend.api.main import app

    return app


@pytest.fixture
def client(tmp_path):
    return TestClient(_fresh_app(tmp_path))


@pytest.fixture
def excel_copy(tmp_path):
    dest = tmp_path / "copy.xlsx"
    shutil.copy(EXCEL_PATH, dest)
    return str(dest)


@pytest.fixture
def seeded_client(client, excel_copy):
    resp = client.post("/ingestion/load", json={"excel_path": excel_copy})
    assert resp.status_code == 200
    return client


# ---- GET /models/5/minimum-funds-required ---------------------------------


def test_minimum_funds_required_endpoint_end_to_end(seeded_client):
    resp = seeded_client.get("/models/5/minimum-funds-required", params={"planning_month": "2026-08"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert data["total"] == pytest.approx(sum(r["required_amount"] for r in data["breakdown"]))
    # Not hardcoded to a specific vendor count — the seeded Excel's vendor
    # total (and how many of them still have a real balance) is
    # environment-dependent (see memory/demo15_dataset_quirks.md).
    vendors_with_balance = [v for v in seeded_client.get("/vendors").json() if v["live_outstanding_balance"] > 1]
    assert len(data["breakdown"]) == len(vendors_with_balance)
    assert data["planning_month"] == "2026-08"
    assert data["as_of"] == "2026-07-01"  # planning month minus one calendar month


def test_minimum_funds_required_missing_planning_month_is_422(seeded_client):
    resp = seeded_client.get("/models/5/minimum-funds-required")
    assert resp.status_code == 422  # FastAPI's own required-query-param validation


def test_minimum_funds_required_bad_format_is_400(seeded_client):
    resp = seeded_client.get("/models/5/minimum-funds-required", params={"planning_month": "2026/08"})
    assert resp.status_code == 400


# ---- Planning month required on first Generate, reused after -------------


def test_generate_plan_requires_planning_month_on_first_call(seeded_client):
    resp = seeded_client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": 1_000_000_000})
    assert resp.status_code == 400


def test_generate_plan_stores_the_actual_planning_month_not_the_ledger_month(seeded_client):
    """docs/14: PlanRun.month for New Model 2 must be the Finance-picked
    planning month, never _current_cycle_month() (the ledger-ingestion
    anchor) — 2030-01 is guaranteed to differ from the real demo sheet's
    ingested months."""
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2030-01"},
    )
    assert resp.status_code == 200

    history = seeded_client.get("/models/5/plan-runs").json()
    assert len(history["plan_runs"]) == 1
    assert history["plan_runs"][0]["month"] == "2030-01-01"


def test_regeneration_reuses_the_stored_planning_month_without_re_prompting(seeded_client):
    first = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert first.status_code == 200

    # Regeneration: no planning_month at all -- must reuse "2026-08", not error.
    second = seeded_client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": 500_000_000})
    assert second.status_code == 200

    history = seeded_client.get("/models/5/plan-runs").json()
    assert len(history["plan_runs"]) == 2
    assert all(run["month"] == "2026-08-01" for run in history["plan_runs"])


def test_generate_plan_succeeds_when_planning_month_equals_sheets_latest_ledger_month(seeded_client):
    """Regression for the real production 400: Finance can set the planning
    month to the SAME cycle a fresh upload already ingested real ledger
    data for (rather than always one month ahead of it, docs/14's assumed
    case) — as_of (planning month minus one) then lands exactly ON the
    sheet's own latest ledger month, so that month's tranche is dated
    strictly after as_of. This used to crash build_weekly_view() (via
    score_vendors() -> compute_vendor_aging() -> bucket_for_months_back())
    with a raw ValueError turned into a 400 by main.py's global handler —
    aging.py now clamps a future-dated tranche into the "0-30" bucket
    instead of raising. Derives planning_month from the real sheet's own
    latest ingested month rather than hardcoding one, so this test doesn't
    rot the next time the real master file's data moves forward.
    """
    from backend.db.models import MonthlyLedger
    from backend.db.session import SessionLocal

    session = SessionLocal()
    try:
        latest_month = session.query(MonthlyLedger.month).order_by(MonthlyLedger.month.desc()).first()[0]
    finally:
        session.close()
    planning_month = f"{latest_month.year:04d}-{latest_month.month:02d}"

    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": planning_month},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "weekly_view" in body


def test_generate_plan_persists_min_funds_required_on_the_plan_run(seeded_client):
    """Analytics funds-trend task: min_funds_required must land on the same
    PlanRun row as funds_figure, populated at Generate Plan time — not left
    NULL, and not backfilled onto any pre-existing row (there are none in a
    freshly seeded DB, so this also implicitly covers that)."""
    from backend.db.models import PlanRun
    from backend.db.session import SessionLocal
    from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL

    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert resp.status_code == 200

    session = SessionLocal()
    try:
        run = (
            session.query(PlanRun)
            .filter(PlanRun.model_used == NEW_MODEL_2_PLAN_RUN_LABEL)
            .order_by(PlanRun.created_at.desc())
            .first()
        )
        assert run is not None
        assert run.funds_figure is not None
        assert run.min_funds_required is not None
        assert float(run.min_funds_required) > 0
    finally:
        session.close()


def test_generate_plan_persists_leftover_remaining_on_the_plan_run(seeded_client):
    """Funds Left card fix: leftover_remaining (allocator.py's real
    post-allocation figure) must land on the same PlanRun row as
    funds_figure/min_funds_required, populated at Generate Plan time — not
    left NULL."""
    from backend.db.models import PlanRun
    from backend.db.session import SessionLocal
    from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL

    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert resp.status_code == 200
    body = resp.json()

    session = SessionLocal()
    try:
        run = (
            session.query(PlanRun)
            .filter(PlanRun.model_used == NEW_MODEL_2_PLAN_RUN_LABEL)
            .order_by(PlanRun.created_at.desc())
            .first()
        )
        assert run is not None
        assert run.leftover_remaining is not None
        assert float(run.leftover_remaining) == pytest.approx(body["plan"]["leftover_remaining"])
    finally:
        session.close()


def test_plan_runs_endpoint_returns_min_funds_required_and_leftover_remaining(seeded_client):
    """Both fields used to be silently dropped by PlanRunOut's response_model
    (undeclared-key bug class) — must actually reach GET /models/5/plan-runs,
    since PlanningView.tsx's loadAll() reads leftover_remaining from there."""
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert resp.status_code == 200

    history = seeded_client.get("/models/5/plan-runs").json()
    latest_run = history["plan_runs"][-1]
    assert latest_run["min_funds_required"] is not None
    assert latest_run["leftover_remaining"] is not None


def test_plan_runs_history_is_empty_before_the_first_generate(seeded_client):
    history = seeded_client.get("/models/5/plan-runs").json()
    assert history == {"plan_runs": [], "vendor_week_distribution_plans": {}}


# ---- Vendor detail card's own Min Funds Required endpoint -----------------


def test_vendor_min_funds_required_endpoint_end_to_end(seeded_client):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.get(
        f"/models/5/vendors/{vendor_id}/min-funds-required", params={"planning_month": "2026-08"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 0
    assert data["rule"].startswith("v2_") or data["rule"].startswith("commitment_")
    assert data["planning_month"] == "2026-08"
    assert data["as_of"] == "2026-07-01"


def test_vendor_min_funds_required_reuses_stored_planning_month_when_omitted(seeded_client):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    generate = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert generate.status_code == 200

    resp = seeded_client.get(f"/models/5/vendors/{vendor_id}/min-funds-required")
    assert resp.status_code == 200
    assert resp.json()["planning_month"] == "2026-08"


def test_vendor_min_funds_required_with_no_plan_yet_and_no_month_is_400(seeded_client):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.get(f"/models/5/vendors/{vendor_id}/min-funds-required")
    assert resp.status_code == 400


# ---- Finalize with no plan yet --------------------------------------------


def test_finalize_with_no_plan_yet_is_a_graceful_ok(seeded_client):
    vendor_count = len(seeded_client.get("/vendors").json())
    resp = seeded_client.post("/models/5/finalize", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["vendor_count"] == vendor_count


# ---- Override editing on a New Model 2 plan_run (plan_history.py fix) -----


def test_override_is_editable_on_the_latest_new_model_2_plan_run(seeded_client):
    """Regression guard for the plan_history.py fix this task required:
    New Model 2's plan_run.month (Finance's picked planning month) diverges
    from the ledger-anchored _current_cycle_month() every other model still
    uses — is_latest_plan_run() must still recognize this plan_run as
    editable, not permanently 409 every override on tab 5.

    Dataset-drift fix (investigated separately): the real master sheet's own
    Assigned Week data lives in a column literally named "Week", not the
    fixed default header "Assigned Week" column_mapping.py expects
    (confirmed, permanent real-world header quirk — memory/
    demo15_dataset_quirks.md). seeded_client ingests via the bare admin-only
    POST /ingestion/load (no AI column-mapping, no stored override lookup),
    so every vendor's assigned_week lands None after ingestion — confirmed
    NOT a planner/allocator bug and NOT specific to planning_month="2030-01"
    (reproduces identically for a realistic month too; build_weekly_view()
    correctly excludes any vendor with no assigned_week by design). Seeding
    one outstanding-balance vendor's assigned_week directly here makes this
    test's actual purpose — override-editability on New Model 2's own
    plan_run — verifiable deterministically, independent of the real
    sheet's own column layout.
    """
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal

    target = next(v for v in seeded_client.get("/vendors").json() if v["live_outstanding_balance"] > 1)
    session = SessionLocal()
    try:
        session.query(Vendor).filter_by(id=target["id"]).update({"assigned_week": 1})
        session.commit()
    finally:
        session.close()

    generate = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2030-01"},
    )
    assert generate.status_code == 200
    detail = generate.json()["weekly_view"]["detail"]
    assert detail, "expected at least one vendor with an assigned week"
    plan_allocation_id = detail[0]["plan_allocation_id"]

    override = seeded_client.patch(
        f"/plan-allocations/{plan_allocation_id}/override", json={"override_amount": 12345.0}
    )
    assert override.status_code == 200
    assert override.json()["override_amount"] == pytest.approx(12345.0)


# ---- Bulk per-vendor Min Funds (planning table's own "Total Min Funds" fix) --


def test_all_vendor_min_funds_required_is_empty_before_any_plan_run(seeded_client):
    """Table must render (not 400) before Finance's first Generate Plan —
    just with an empty breakdown, so the frontend shows "—" per vendor."""
    resp = seeded_client.get("/models/5/vendor-min-funds-required")
    assert resp.status_code == 200
    data = resp.json()
    assert data["planning_month"] is None
    assert data["as_of"] is None
    assert data["breakdown"] == []


def test_all_vendor_min_funds_required_matches_the_single_vendor_endpoint(seeded_client):
    """Every vendor's figure in the bulk breakdown must equal what the
    single-vendor detail-card endpoint would say for that same vendor —
    one rule, two call shapes, never allowed to drift."""
    generate = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert generate.status_code == 200

    bulk = seeded_client.get("/models/5/vendor-min-funds-required")
    assert bulk.status_code == 200
    bulk_data = bulk.json()
    assert bulk_data["planning_month"] == "2026-08"
    assert bulk_data["as_of"] == "2026-07-01"
    assert bulk_data["breakdown"], "expected at least one vendor with an outstanding balance"

    for row in bulk_data["breakdown"]:
        single = seeded_client.get(f"/models/5/vendors/{row['vendor_id']}/min-funds-required")
        assert single.status_code == 200
        assert single.json()["total"] == pytest.approx(row["required_amount"], abs=1)


# ---- Reset cycle -----------------------------------------------------------


def test_reset_cycle_clears_current_month_plan_and_vendor_state_and_audits(seeded_client, excel_copy):
    """Reset wipes this cycle's PlanRun/PlanAllocation rows and every
    vendor's whole-month cycle state (reusing _reset_vendor_cycle_state()),
    adds exactly one audit-log entry, and never writes to the master Excel."""
    from datetime import date

    from backend.db.models import PlanAllocation, PlanRun, Vendor
    from backend.db.session import SessionLocal
    from backend.shared.enums import PaymentStatus

    with open(excel_copy, "rb") as f:
        excel_before = f.read()

    # PlanAllocation rows are only persisted for vendors with a real
    # assigned_week (planner.py's build_weekly_view) — the live master
    # sheet can (and currently does) leave that column entirely unset, so
    # one outstanding vendor is stamped with an assigned_week directly here
    # rather than depending on the sheet's own current state for a row to
    # exist at all.
    session = SessionLocal()
    try:
        vendor_row = session.query(Vendor).filter(Vendor.opening_balance > Vendor.paid_so_far_this_month).first()
        assert vendor_row is not None, "expected at least one vendor with an outstanding balance"
        vendor_row.assigned_week = 1
        vendor_id = vendor_row.id
        session.commit()
    finally:
        session.close()

    gen = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert gen.status_code == 200
    plan_run_id = gen.json()["weekly_view"]["plan_run_id"]

    session = SessionLocal()
    try:
        allocation = session.query(PlanAllocation).filter_by(plan_run_id=plan_run_id, vendor_id=vendor_id).first()
        assert allocation is not None, "expected a persisted allocation for the stamped vendor"
        allocation_id = allocation.id
    finally:
        session.close()

    override_resp = seeded_client.patch(
        f"/plan-allocations/{allocation_id}/override", json={"override_amount": 1.0}
    )
    assert override_resp.status_code == 200

    # Simulate the rest of the cycle state Reset must also clear — set
    # directly since reconsider/payment_status/finalized_budget_amount
    # aren't all reachable through a dedicated PATCH endpoint.
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.reconsider = True
        vendor.payment_status = PaymentStatus.PAID_IN_FULL
        vendor.paid_so_far_this_month = 500.0
        vendor.finalized_budget_amount = 1234.0
        session.commit()
    finally:
        session.close()

    audit_before = seeded_client.get("/audit-log").json()["total"]

    resp = seeded_client.post("/models/5/reset-cycle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["planning_month"] == "2026-08"
    assert body["deleted_plan_runs"] >= 1
    assert body["deleted_allocations"] >= 1
    assert body["vendors_reset"] > 0

    session = SessionLocal()
    try:
        assert session.query(PlanRun).filter(PlanRun.month == date(2026, 8, 1)).count() == 0
        assert session.query(PlanAllocation).count() == 0

        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        assert vendor.override_amount is None
        assert vendor.reconsider is None
        assert vendor.payment_status == PaymentStatus.NOT_PAID
        assert float(vendor.paid_so_far_this_month) == 0
        assert vendor.finalized_budget_amount is None
        assert vendor.week_distribution_plan is None
    finally:
        session.close()

    audit_after = seeded_client.get("/audit-log").json()
    assert audit_after["total"] == audit_before + 1
    reset_entries = [e for e in audit_after["items"] if e["field_name"] == "planning_cycle_reset"]
    assert len(reset_entries) == 1

    # Same "no plan exists yet" empty shape the Planning tab shows right
    # after upload, before Min Funds has ever been calculated.
    history = seeded_client.get("/models/5/plan-runs").json()
    assert history == {"plan_runs": [], "vendor_week_distribution_plans": {}}

    with open(excel_copy, "rb") as f:
        excel_after = f.read()
    assert excel_after == excel_before


def test_reset_cycle_with_nothing_generated_yet_is_400(seeded_client):
    resp = seeded_client.post("/models/5/reset-cycle")
    assert resp.status_code == 400


def test_reset_cycle_leaves_a_different_already_finalized_past_month_untouched(seeded_client):
    """The two things most important not to break: an older, already-
    finalized PlanRun/PlanAllocation for a DIFFERENT month must survive
    byte-for-byte in the DB, and the Planning tab must resolve back to the
    (now plan-less) CURRENT cycle afterward — not silently fall back to
    that older month just because it's the only PlanRun row left.

    The past plan_run is inserted directly (not via generate_plan()) so
    this test only exercises Reset's own DB scoping/month-resolution, not
    the allocator's own month-dependent business logic."""
    from datetime import date, datetime

    from backend.db.models import PlanAllocation, PlanRun
    from backend.db.session import SessionLocal
    from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL
    from backend.shared.planning_month_store import set_current_planning_month

    vendor_id = seeded_client.get("/vendors").json()[0]["id"]

    session = SessionLocal()
    try:
        past_run = PlanRun(
            created_at=datetime(2026, 6, 15),
            month=date(2026, 6, 1),
            model_used=NEW_MODEL_2_PLAN_RUN_LABEL,
            funds_figure=500_000.0,
            min_funds_required=400_000.0,
            leftover_remaining=100_000.0,
        )
        session.add(past_run)
        session.flush()
        session.add(
            PlanAllocation(
                plan_run_id=past_run.id,
                vendor_id=vendor_id,
                assigned_week=1,
                within_week_order=1,
                allocated_amount=250_000.0,
                override_amount=None,
                required_amount_snapshot=250_000.0,
            )
        )
        past_plan_run_id = past_run.id
        session.commit()
    finally:
        session.close()

    current = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert current.status_code == 200

    session = SessionLocal()
    try:
        set_current_planning_month(session, "2026-08")
        session.commit()
    finally:
        session.close()

    resp = seeded_client.post("/models/5/reset-cycle")
    assert resp.status_code == 200
    assert resp.json()["planning_month"] == "2026-08"

    session = SessionLocal()
    try:
        past_run_after = session.query(PlanRun).filter_by(id=past_plan_run_id).one()
        assert past_run_after.month == date(2026, 6, 1)
        assert float(past_run_after.funds_figure) == pytest.approx(500_000.0)
        allocations_after = session.query(PlanAllocation).filter_by(plan_run_id=past_plan_run_id).all()
        assert len(allocations_after) == 1
        assert float(allocations_after[0].allocated_amount) == pytest.approx(250_000.0)
    finally:
        session.close()

    # Must resolve back to the current (2026-08) cycle, not fall back to the
    # older still-existing 2026-06 plan_run now that it's the only one left.
    history = seeded_client.get("/models/5/plan-runs").json()
    assert history == {"plan_runs": [], "vendor_week_distribution_plans": {}}


if __name__ == "__main__":
    print("run via pytest — uses fixtures (client/seeded_client), no bare self-check here")
