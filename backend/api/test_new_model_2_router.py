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
    editable, not permanently 409 every override on tab 5."""
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


if __name__ == "__main__":
    print("run via pytest — uses fixtures (client/seeded_client), no bare self-check here")
