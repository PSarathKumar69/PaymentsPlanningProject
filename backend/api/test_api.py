"""Self-check for the FastAPI layer (backend/api/). Run with:
pytest backend/api/test_api.py

Never touches the real backend/db/app.db or data/Vendor's Details.xlsx.
Follows the same isolation convention already used by
backend/ingestion/test_load_excel.py and backend/ingestion/test_upload.py:
point PAYMENTS_DB_PATH at a fresh temp file and pop every cached `backend.*`
module so the whole import graph (app -> routers -> models -> db.session)
re-binds to that temp DB. A temp copy of the real Excel is used for
ingestion, so `seeded_client` gets real vendor data without ever opening
the real file for writing.

Doesn't re-test model/deterministic-layer correctness (already covered by
the existing 89 tests) — only that each route maps request -> underlying
function -> response JSON, and that ValueError surfaces as 400.
"""
import shutil
import sys
from datetime import date, timedelta

import os

import pytest
from fastapi.testclient import TestClient

from backend.ingestion.load_excel import EXCEL_PATH


def _fresh_app(tmp_path):
    """Forces the entire backend.* import graph to re-bind against a brand
    new temp SQLite file, same re-import trick the existing ingestion tests
    use — just applied to every backend module instead of a hand-picked
    few, since the API pulls in the whole graph at once."""
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
    """A client whose (temp) DB has been populated from a temp Excel copy —
    exercises /ingestion/load end to end as a side effect of setup."""
    resp = client.post("/ingestion/load", json={"excel_path": excel_copy})
    assert resp.status_code == 200
    return client


# ---- Vendors ----------------------------------------------------------


def test_list_and_get_vendor(seeded_client):
    resp = seeded_client.get("/vendors")
    assert resp.status_code == 200
    vendors = resp.json()
    assert len(vendors) == 83

    vendor_id = vendors[0]["id"]
    resp = seeded_client.get(f"/vendors/{vendor_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == vendor_id


def test_vendor_aging_and_payments(seeded_client):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]

    resp = seeded_client.get(f"/vendors/{vendor_id}/aging")
    assert resp.status_code == 200
    assert "total_outstanding" in resp.json()

    resp = seeded_client.get(f"/vendors/{vendor_id}/payments")
    assert resp.status_code == 200
    assert resp.json() == []  # nothing logged yet


@pytest.mark.parametrize("erp_code", ["V00193", "V00204", "V00194", "V00393", "V00226"])
def test_vendor_aging_min_funds_required_matches_the_planning_table_rule(seeded_client, erp_code):
    """Bug fix: GET /vendors/{id}/aging and GET /vendors/aging used to call
    the retired old-model min_funds.min_funds_tranche_breakdown() — for
    these 5 real vendors that disagrees with the Planning table's own
    required_amount_v2() figure (the old rule returns 0.0 for all five;
    their real outstanding money is in an older month the old Must Pay
    rule — "last month's own payable only" — never looks at). Confirmed
    real cases from the min-funds-verification-export Exceptions
    investigation. Both the single-vendor and bulk aging endpoints must
    now agree with the Planning table for every one of them.
    """
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal
    from backend.shared.min_funds_v2 import required_amount_v2

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code=erp_code).one()
        vendor_id = vendor.id
        ledger_rows = session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).order_by(MonthlyLedger.month).all()
        expected_total, _ = required_amount_v2(vendor, ledger_rows)
    finally:
        session.close()

    single = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    assert single["min_funds_required"] == pytest.approx(expected_total, abs=0.01)
    assert sum(t["remaining_amount"] for t in single["min_funds_tranches"]) == pytest.approx(expected_total, abs=0.01)

    bulk = seeded_client.get("/vendors/aging").json()
    bulk_item = next(item for item in bulk if item["vendor_id"] == vendor_id)
    assert bulk_item["min_funds_required"] == pytest.approx(expected_total, abs=0.01)
    assert single["min_funds_required"] == bulk_item["min_funds_required"]


def test_vendor_categories_endpoint_matches_the_shared_enum(seeded_client):
    """P2 demo-polish task: the frontend's category dropdown reads from
    here instead of hardcoding its own copy — confirms every
    backend/shared/enums.py::VendorCategory member is present with a
    non-empty label, and that this static route isn't shadowed by the
    dynamic /vendors/{vendor_id} route registered after it."""
    from backend.shared.enums import VendorCategory

    resp = seeded_client.get("/vendors/categories")
    assert resp.status_code == 200
    categories = resp.json()
    assert {c["value"] for c in categories} == {c.value for c in VendorCategory}
    assert all(c["label"] for c in categories)


def test_patch_vendor_writes_db_and_audit_log(seeded_client, tmp_path):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    patch_excel = str(tmp_path / "patch_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)

    resp = seeded_client.patch(
        f"/vendors/{vendor_id}",
        json={"field": "commitment_months", "new_value": 3, "excel_path": patch_excel},
    )
    assert resp.status_code == 200
    assert resp.json()["new_value"] == 3

    resp = seeded_client.get(f"/vendors/{vendor_id}")
    assert resp.json()["commitment_months"] == 3

    resp = seeded_client.get("/audit-log", params={"vendor_id": vendor_id})
    assert resp.status_code == 200
    assert any(row["field_name"] == "commitment_months" for row in resp.json()["items"])


def test_patch_vendor_invalid_field_is_400(seeded_client):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.patch(f"/vendors/{vendor_id}", json={"field": "not_a_real_field", "new_value": 1})
    assert resp.status_code == 400
    assert "unknown field" in resp.json()["detail"]


# ---- AI layer — talking scripts (Gemini mocked, never a real network call) --------


def _seed_zero_status_vendor(client, tmp_path):
    """New Model 2's floor percentages (10%/25%) mean a real vendor's
    allocation almost never rounds to literal zero purely from a tiny
    available_funds figure (unlike the old score-based models) — a vendor
    with a near-zero required_amount is what actually clamps to "zero"
    status. Tags one real vendor Normal/P2 with a tiny (2.0) balance --
    above has_outstanding_balance()'s MONEY_EPSILON floor (so it isn't
    filtered out of the plan entirely) but small enough that
    `allocated = required_amount * floor_pct(25%) = 0.5` still lands under
    MONEY_EPSILON, regardless of what the rest of the real dataset looks
    like.

    Bug fix: these PATCHes used to omit `excel_path`, so update_vendor_field()
    fell back to its own default (the real EXCEL_PATH) and wrote straight
    into the real production master Excel — confirmed to actually create a
    stray "Priority Tag" column there (the real header is "Prioity") and
    silently overwrite a real vendor's Category cell. Every vendor-editing
    PATCH in this file must pass its own tmp_path copy, never the real file.
    """
    patch_excel = str(tmp_path / "seed_zero_status_patch.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    vendor_id = _seed_known_balance_vendor(client, amount=2.0)
    client.patch(
        f"/vendors/{vendor_id}", json={"field": "category", "new_value": "normal", "excel_path": patch_excel}
    )
    client.patch(
        f"/vendors/{vendor_id}", json={"field": "priority_tag", "new_value": "P2", "excel_path": patch_excel}
    )
    return vendor_id


def test_ai_talking_scripts_only_for_zero_status_rows(seeded_client, monkeypatch, tmp_path):
    import backend.ai_layer.gemini_client as gemini_client

    monkeypatch.setattr(
        gemini_client,
        "generate_text",
        lambda prompt: "**Why we're not paying this cycle**\n- test\n\n**Talking points (step by step)**\n- test",
    )
    _seed_zero_status_vendor(seeded_client, tmp_path)

    plan_resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 0, "planning_month": "2026-08"},
    )
    assert plan_resp.status_code == 200
    allocations = plan_resp.json()["plan"]["allocations"]
    zero_count = sum(1 for a in allocations if a["status"] == "zero")
    assert zero_count > 0

    resp = seeded_client.post("/ai/talking-scripts", json={"vendor_allocations": allocations})
    assert resp.status_code == 200
    scripts = resp.json()["scripts"]
    assert len(scripts) == zero_count
    assert all("Why we're not paying this cycle" in s["script_text"] for s in scripts)


def test_ai_talking_scripts_missing_key_is_400(seeded_client, monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-api-key-here")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    _seed_zero_status_vendor(seeded_client, tmp_path)

    plan_resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 0, "planning_month": "2026-08"},
    )
    zero_row = next(a for a in plan_resp.json()["plan"]["allocations"] if a["status"] == "zero")

    resp = seeded_client.post("/ai/talking-scripts", json={"vendor_allocations": [zero_row]})
    assert resp.status_code == 400
    assert "GEMINI_API_KEY" in resp.json()["detail"]


def test_patch_vendor_invalid_category_value_is_400(seeded_client):
    """Proves the global ValueError->400 handler (main.py), not a per-route
    try/except: a real validation failure inside update_vendor_field's
    _validate() surfaces as 400 with its message, same as any other route
    hitting the same handler."""
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.patch(f"/vendors/{vendor_id}", json={"field": "category", "new_value": "not_a_real_category"})
    assert resp.status_code == 400
    assert "invalid category" in resp.json()["detail"]


# ---- New Model 2 (router-level, generic contract — see also
# test_new_model_2_router.py for planning-month-specific coverage) ----------
# Model 1/2/3 and New Model 1's own generate-plan/scores/buckets endpoints
# were removed once New Model 2 became the sole model — their tests went
# with them. What's below re-targets the tests that were really proving
# generic, model-agnostic router contracts (build_weekly_view's shape,
# override/plan-history/week-distribution behavior) at New Model 2 instead
# of deleting that coverage outright.

NM2_PLANNING_MONTH = "2026-08"  # arbitrary but fixed, matches test_new_model_2_router.py's own convention


# ---- Planning month persistence (Main-tab upload-confirm task) -----------


def test_minimum_funds_required_no_planning_month_is_400_when_nothing_resolvable(seeded_client):
    """seeded_client only ever ran /ingestion/load — no plan_run, and no
    planning_month ever confirmed via commit-upload — so the newly-optional
    param must still 400 rather than silently guessing a month."""
    resp = seeded_client.get("/models/5/minimum-funds-required")
    assert resp.status_code == 400


def _commit_upload_with_planning_month(client, excel_copy, planning_month):
    # Vercel-migration task: commit-upload's default (no excel_path override)
    # now writes the master workbook into this test's own isolated temp DB
    # (PAYMENTS_DB_PATH), not a real file — no more need to monkeypatch
    # upload_module.EXCEL_PATH/MASTER_EXCEL_BACKUP_PATH to redirect it away
    # from the real master file, since there's no file involved at all.
    with open(excel_copy, "rb") as f:
        resp = client.post(
            "/master-data/commit-upload",
            data={"planning_month": planning_month},
            files={"file": ("upload.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert resp.status_code == 200
    return resp


def test_minimum_funds_required_no_planning_month_resolves_from_persisted_config(seeded_client, excel_copy):
    _commit_upload_with_planning_month(seeded_client, excel_copy, NM2_PLANNING_MONTH)
    resp = seeded_client.get("/models/5/minimum-funds-required")
    assert resp.status_code == 200
    assert resp.json()["planning_month"] == NM2_PLANNING_MONTH


def test_current_planning_month_endpoint_reflects_persisted_config_then_latest_plan_run(seeded_client, excel_copy):
    resp = seeded_client.get("/models/5/current-planning-month")
    assert resp.json()["planning_month"] is None  # nothing resolvable yet

    _commit_upload_with_planning_month(seeded_client, excel_copy, NM2_PLANNING_MONTH)
    resp = seeded_client.get("/models/5/current-planning-month")
    assert resp.json()["planning_month"] == NM2_PLANNING_MONTH

    # Once a plan_run exists, that (not the persisted config value) wins —
    # same fallback order _resolve_planning_month() itself uses.
    later_month = "2026-09"
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": later_month},
    )
    assert resp.status_code == 200
    resp = seeded_client.get("/models/5/current-planning-month")
    assert resp.json()["planning_month"] == later_month


def test_generate_plan_falls_back_to_persisted_planning_month_when_no_plan_run_exists(seeded_client, excel_copy):
    """Nothing explicit passed in the request body, and no PlanRun exists yet
    this cycle — must still resolve via the persisted config value set by a
    prior commit-upload, not 400."""
    _commit_upload_with_planning_month(seeded_client, excel_copy, NM2_PLANNING_MONTH)
    resp = seeded_client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": 100_000})
    assert resp.status_code == 200
    resp = seeded_client.get("/models/5/plan-runs")
    assert resp.json()["plan_runs"][-1]["month"].startswith(NM2_PLANNING_MONTH)


def test_new_model_2_generate_plan_and_weekly_view_returns_both_pieces(seeded_client):
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": NM2_PLANNING_MONTH},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "plan" in body and "weekly_view" in body
    assert "allocations" in body["plan"]
    assert "detail" in body["weekly_view"] and "weekly_summary" in body["weekly_view"]


def test_new_model_2_weekly_view_detail_rows_carry_plan_allocation_id_and_effective_amount(seeded_client):
    """The override endpoint needs a plan_allocation_id to target — confirms
    every persisted detail row has one, and a freshly-generated plan (no
    override yet) has override_amount=null and effective_amount ==
    allocated_amount."""
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": NM2_PLANNING_MONTH},
    )
    assert resp.status_code == 200
    detail = resp.json()["weekly_view"]["detail"]
    assert len(detail) > 0
    for row in detail:
        assert row["plan_allocation_id"] is not None
        assert row["override_amount"] is None
        assert row["effective_amount"] == pytest.approx(row["actual_planned"])


def test_new_model_2_weekly_view_detail_rows_carry_week_distribution_plan_and_actual_paid(seeded_client):
    """week_distribution_plan (display-only, Finance-editable) defaults to
    the full suggested amount under the vendor's own assigned_week; a fresh
    plan with no payments logged yet has an empty week_actual_paid."""
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": NM2_PLANNING_MONTH},
    )
    assert resp.status_code == 200
    detail = resp.json()["weekly_view"]["detail"]
    assert len(detail) > 0
    for row in detail:
        assert row["week_distribution_plan"] == {str(row["assigned_week"]): pytest.approx(row["actual_planned"])}
        assert row["week_actual_paid"] == {}


# ---- Plan allocation overrides ---------------------------------------------


def _first_nm2_allocation_row(client, available_funds=100_000, planning_month=NM2_PLANNING_MONTH):
    resp = client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": available_funds, "planning_month": planning_month},
    )
    weekly_view = resp.json()["weekly_view"]
    detail = weekly_view["detail"]
    assert detail, "need at least one vendor with an assigned_week to have a plan_allocation row"
    # plan_run_id lives on weekly_view itself, not per detail row — folded in
    # here so callers can treat the row as self-contained.
    return {**detail[0], "plan_run_id": weekly_view["plan_run_id"]}


def _nm2_row_for_vendor(client, vendor_id, available_funds=100_000):
    """Same generate-plan-and-weekly-view call as above, but for a SPECIFIC
    vendor — needed to build a "Plan 1, Plan 2, ..." sequence for one vendor
    across repeated calls, since detail[0] isn't guaranteed to stay the same
    vendor once funds/overrides change between calls. No planning_month —
    New Model 2 reuses whatever was stored on the first call this cycle."""
    resp = client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": available_funds})
    weekly_view = resp.json()["weekly_view"]
    detail = weekly_view["detail"]
    row = next((r for r in detail if r["vendor_id"] == vendor_id), None)
    assert row is not None, f"vendor {vendor_id} missing from New Model 2's detail this round"
    return {**row, "plan_run_id": weekly_view["plan_run_id"]}


def test_override_endpoint_valid_override_succeeds_and_audit_logs(seeded_client):
    row = _first_nm2_allocation_row(seeded_client)
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    override_amount = float(vendor["opening_balance"]) * 0.9  # within bounds, above the suggested amount

    resp = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_allocation_id"] == row["plan_allocation_id"]
    assert body["vendor_id"] == row["vendor_id"]
    assert body["allocated_amount"] == pytest.approx(row["actual_planned"])
    assert body["override_amount"] == pytest.approx(override_amount)
    assert body["effective_amount"] == pytest.approx(override_amount)

    audit = seeded_client.get("/audit-log", params={"vendor_id": row["vendor_id"]}).json()["items"]
    assert any(
        a["field_name"] == "vendor.override_amount" and float(a["new_value"]) == pytest.approx(override_amount)
        for a in audit
    )


def test_override_endpoint_rejects_above_opening_balance(seeded_client):
    row = _first_nm2_allocation_row(seeded_client)
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    too_much = float(vendor["opening_balance"]) * 1.5

    resp = seeded_client.patch(f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": too_much})
    assert resp.status_code == 400

    # Rejected — override must not have been written to the audit log.
    audit = seeded_client.get("/audit-log", params={"vendor_id": row["vendor_id"]}).json()["items"]
    assert not any(a["new_value"] and float(a["new_value"]) == pytest.approx(too_much) for a in audit)


def test_override_endpoint_null_clears_override(seeded_client):
    row = _first_nm2_allocation_row(seeded_client)
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    override_amount = float(vendor["opening_balance"]) * 0.9

    seeded_client.patch(f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount})

    resp = seeded_client.patch(f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["override_amount"] is None
    assert body["effective_amount"] == pytest.approx(body["allocated_amount"])


def test_override_endpoint_funds_warning_present_when_committed_exceeds_funds(seeded_client):
    """docs/06 fix: non-blocking — the save still succeeds (200), but the
    response flags that this plan_run's total committed amount now exceeds
    its funds_figure."""
    row = _first_nm2_allocation_row(seeded_client, available_funds=100_000)
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    override_amount = float(vendor["opening_balance"]) * 0.9  # alone exceeds the round's 100,000 funds figure

    resp = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["funds_warning"] is not None
    assert body["funds_warning"]["available_funds"] == pytest.approx(100_000)
    assert body["funds_warning"]["total_committed"] >= override_amount - 1
    assert body["funds_warning"]["over_by"] == pytest.approx(
        body["funds_warning"]["total_committed"] - body["funds_warning"]["available_funds"]
    )


def test_override_endpoint_funds_warning_absent_when_comfortably_under(seeded_client):
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": NM2_PLANNING_MONTH},
    )
    row = resp.json()["weekly_view"]["detail"][0]
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    override_amount = min(float(vendor["opening_balance"]) * 0.01, 1000.0)  # tiny, comfortably under the huge funds pool

    resp = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount}
    )
    assert resp.status_code == 200
    assert resp.json()["funds_warning"] is None


# ---- Plan-run history + delete (new: multi-generation history feature) ----


def test_plan_runs_history_endpoint_orders_plans_oldest_first_and_numbers_them(seeded_client):
    first = _first_nm2_allocation_row(seeded_client, available_funds=100_000)
    vendor_id = first["vendor_id"]
    second = _nm2_row_for_vendor(seeded_client, vendor_id, available_funds=150_000)

    history = seeded_client.get("/models/5/plan-runs").json()
    plan_run_ids = [p["plan_run_id"] for p in history["plan_runs"]]
    assert plan_run_ids.index(first["plan_run_id"]) < plan_run_ids.index(second["plan_run_id"])

    # "Plan N" numbering = 1-based position in this oldest-first list.
    plan_1 = history["plan_runs"][plan_run_ids.index(first["plan_run_id"])]
    plan_2 = history["plan_runs"][plan_run_ids.index(second["plan_run_id"])]
    assert any(
        a["vendor_id"] == vendor_id and a["allocated_amount"] == pytest.approx(first["actual_planned"])
        for a in plan_1["allocations"]
    )
    assert any(a["vendor_id"] == vendor_id for a in plan_2["allocations"])


def test_plan_runs_history_treats_generate_and_regenerate_as_one_continuous_sequence(seeded_client):
    """New Model 2's first Generate Plan is "Plan 1"; every subsequent
    Regenerate (same endpoint, no planning_month needed after the first
    call — docs/14) is Plan 2, Plan 3... — one continuous numbered
    sequence."""
    gen = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": NM2_PLANNING_MONTH},
    )
    plan_1_id = gen.json()["weekly_view"]["plan_run_id"]

    regen1 = seeded_client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": 120_000})
    plan_2_id = regen1.json()["weekly_view"]["plan_run_id"]

    regen2 = seeded_client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": 140_000})
    plan_3_id = regen2.json()["weekly_view"]["plan_run_id"]

    history = seeded_client.get("/models/5/plan-runs").json()
    ids = [p["plan_run_id"] for p in history["plan_runs"]]
    assert ids == [plan_1_id, plan_2_id, plan_3_id]


def test_delete_plan_run_hard_deletes_audits_and_latest_falls_back(seeded_client):
    first = _first_nm2_allocation_row(seeded_client, available_funds=100_000)
    vendor_id = first["vendor_id"]
    second = _nm2_row_for_vendor(seeded_client, vendor_id, available_funds=150_000)

    audit_before = seeded_client.get("/audit-log").json()["total"]

    resp = seeded_client.delete(f"/plan-runs/{second['plan_run_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_run_id"] == second["plan_run_id"]
    assert body["deleted_allocations"] >= 1

    history = seeded_client.get("/models/5/plan-runs").json()
    ids = [p["plan_run_id"] for p in history["plan_runs"]]
    assert second["plan_run_id"] not in ids  # hard-deleted, not soft-flagged
    assert first["plan_run_id"] in ids  # untouched

    audit_after = seeded_client.get("/audit-log").json()
    assert audit_after["total"] == audit_before + 1  # exactly one new entry, no AuditLog row removed
    assert audit_after["items"][0]["field_name"] == "plan_run_deleted"  # order_by(id.desc()) -> most recent first
    assert str(second["plan_run_id"]) in audit_after["items"][0]["old_value"]

    # Latest-plan_run fallback (confirmed, intended behavior, not a bug to
    # guard against): with Plan 2 gone, Plan 1 is now correctly recognized
    # as the latest — its override becomes editable again.
    resp = seeded_client.patch(
        f"/plan-allocations/{first['plan_allocation_id']}/override", json={"override_amount": 1234.0}
    )
    assert resp.status_code == 200


def test_delete_plan_run_404_for_unknown_id(seeded_client):
    resp = seeded_client.delete("/plan-runs/999999999")
    assert resp.status_code == 404


def test_override_patch_rejected_on_non_latest_plan_run_succeeds_on_latest(seeded_client):
    first = _first_nm2_allocation_row(seeded_client, available_funds=100_000)
    vendor_id = first["vendor_id"]
    second = _nm2_row_for_vendor(seeded_client, vendor_id, available_funds=150_000)
    assert first["plan_allocation_id"] != second["plan_allocation_id"]

    stale_resp = seeded_client.patch(
        f"/plan-allocations/{first['plan_allocation_id']}/override", json={"override_amount": 1000.0}
    )
    assert stale_resp.status_code == 409

    latest_resp = seeded_client.patch(
        f"/plan-allocations/{second['plan_allocation_id']}/override", json={"override_amount": 1000.0}
    )
    assert latest_resp.status_code == 200


# ---- Week distribution plan (display-only, non-enforced) ------------------


def test_week_distribution_patch_partial_update_only_touches_specified_weeks(seeded_client):
    row = _first_nm2_allocation_row(seeded_client)
    default_plan = row["week_distribution_plan"]  # {assigned_week: full suggested amount}

    resp = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/week-distribution", json={"updates": {"3": 12345.0}}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_allocation_id"] == row["plan_allocation_id"]
    assert body["vendor_id"] == row["vendor_id"]
    # The new week is merged in; every previously-stored week is untouched.
    expected = {**default_plan, "3": 12345.0}
    assert body["week_distribution_plan"] == pytest.approx(expected)

    # A second, different partial update only touches its own week — the
    # first edit ("3") and the original default must both still be there.
    resp2 = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/week-distribution", json={"updates": {"5": 999.0}}
    )
    assert resp2.status_code == 200
    assert resp2.json()["week_distribution_plan"] == pytest.approx({**expected, "5": 999.0})


def test_week_distribution_patch_has_no_ledger_integrity_or_sum_validation(seeded_client):
    """Confirmed decision 1: purely additive, non-enforced — a distribution
    that doesn't sum to the allocated/suggested amount, or numbers with no
    real-world basis, must still succeed."""
    row = _first_nm2_allocation_row(seeded_client)
    huge = row["actual_planned"] * 100 + 999_999_999  # deliberately absurd, far above anything owed

    resp = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/week-distribution", json={"updates": {"1": huge, "2": -50.0}}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["week_distribution_plan"]["1"] == pytest.approx(huge)
    assert body["week_distribution_plan"]["2"] == pytest.approx(-50.0)


def test_week_distribution_patch_writes_an_audit_log_entry(seeded_client):
    row = _first_nm2_allocation_row(seeded_client)
    resp = seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/week-distribution", json={"updates": {"2": 42.0}}
    )
    assert resp.status_code == 200

    audit = seeded_client.get("/audit-log", params={"vendor_id": row["vendor_id"]}).json()["items"]
    assert any(
        a["field_name"] == "plan_allocation.week_distribution_plan" and '"2": 42.0' in a["new_value"] for a in audit
    )


def test_week_distribution_patch_never_changes_payment_status_or_override_math(seeded_client):
    """Confirmed decision: purely additive — editing the planned split must
    never touch payment_status, ledger-integrity, or override math."""
    row = _first_nm2_allocation_row(seeded_client)
    vendor_before = seeded_client.get(f"/vendors/{row['vendor_id']}").json()

    seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/week-distribution",
        json={"updates": {"1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0, "5": 5.0}},
    )

    vendor_after = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    assert vendor_after["payment_status"] == vendor_before["payment_status"]
    assert vendor_after["paid_so_far_this_month"] == vendor_before["paid_so_far_this_month"]

    # Re-read the same allocation directly (not a fresh plan_run) to confirm
    # the week-distribution edit didn't set/clear override_amount as a side effect.
    from backend.db.models import PlanAllocation
    from backend.db.session import SessionLocal

    session = SessionLocal()
    try:
        allocation = session.query(PlanAllocation).filter_by(id=row["plan_allocation_id"]).one()
        assert allocation.override_amount is None
        assert float(allocation.allocated_amount) == pytest.approx(row["actual_planned"])
    finally:
        session.close()


# ---- New Model 2 override exclusion on regeneration ------------------------


def test_new_model_2_no_overrides_totals_are_zero_and_funds_unreduced(seeded_client):
    """Edge case: nothing overridden -> total_overridden is 0.0 and
    funds_left_for_regeneration equals available_funds exactly, same as
    today's behavior."""
    available_funds = 5_000_000.0
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": available_funds, "planning_month": NM2_PLANNING_MONTH},
    ).json()
    assert resp["total_overridden"] == 0.0
    assert resp["funds_left_for_regeneration"] == pytest.approx(available_funds)
    assert all(row["status"] != "override_locked" for row in resp["plan"]["allocations"])


def test_new_model_2_override_excludes_vendor_and_reduces_funds_pool_on_regeneration(seeded_client):
    """Full scenario: generate with available_funds, override one vendor,
    regenerate -- the overridden vendor's Suggested figure freezes at their
    pre-override amount (not recomputed, not mirroring the override), every
    other vendor is computed against available_funds minus the override, and
    a second regeneration with a DIFFERENT override amount on the same
    vendor still excludes them correctly with the updated figure."""
    available_funds = 5_000_000.0

    first = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": available_funds, "planning_month": NM2_PLANNING_MONTH},
    ).json()
    assert first["total_overridden"] == 0.0
    assert first["funds_left_for_regeneration"] == pytest.approx(available_funds)

    detail = first["weekly_view"]["detail"]
    assert detail, "need at least one assigned-week vendor to override"
    row = detail[0]
    vendor_id = row["vendor_id"]
    suggested_before_override = row["actual_planned"]  # original suggested figure, before any override

    vendor = seeded_client.get(f"/vendors/{vendor_id}").json()
    override_amount = min(suggested_before_override * 1.5, float(vendor["opening_balance"]))
    seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount}
    )

    second = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view", json={"available_funds": available_funds}
    ).json()

    assert second["total_overridden"] == pytest.approx(override_amount)
    assert second["funds_left_for_regeneration"] == pytest.approx(available_funds - override_amount)

    allocations = {a["vendor_id"]: a for a in second["plan"]["allocations"]}
    overridden_row = allocations[vendor_id]
    assert overridden_row["status"] == "override_locked"
    # Frozen at the PRE-override suggested figure -- not recomputed, and not
    # simply mirroring the override amount just set above.
    assert overridden_row["allocated_amount"] == pytest.approx(suggested_before_override, abs=1)
    assert overridden_row["allocated_amount"] != pytest.approx(override_amount, abs=1)

    # Independently recompute what every OTHER vendor should get, run
    # directly against the reduced pool (available_funds - override_amount)
    # with the overridden vendor excluded from the eligible set entirely --
    # then compare against the API's own figure for a sampled vendor.
    from backend.db.session import SessionLocal
    from backend.models.new_model_2.allocator import generate_plan

    session = SessionLocal()
    try:
        all_vendor_ids = {v["id"] for v in seeded_client.get("/vendors").json()}
        # as_of must match what the router itself resolved (planning_month
        # minus one calendar month, docs/14) — NM2_PLANNING_MONTH="2026-08"
        # -> as_of=2026-07-01. Passing the default as_of=None here would
        # compute required_amount_v2 against a different "current" bucket
        # than the router just used, comparing two different numbers.
        expected = generate_plan(
            available_funds - override_amount,
            session=session,
            as_of=date(2026, 7, 1),
            eligible_vendor_ids=all_vendor_ids - {vendor_id},
        )
    finally:
        session.close()
    expected_by_vendor = expected["allocations"].set_index("vendor_id")["allocated_amount"]

    other_vendor_id = next(vid for vid in allocations if vid != vendor_id)
    assert allocations[other_vendor_id]["allocated_amount"] == pytest.approx(
        expected_by_vendor[other_vendor_id], abs=1
    )

    # Second regeneration, a DIFFERENT override amount on the SAME vendor --
    # still excluded, still frozen at the same original suggested figure,
    # funds pool reduced by the NEW override amount. Must target the
    # vendor's LATEST plan_allocation_id (from `second`'s own weekly_view
    # detail, not the stale one captured from `first`) -- the plan-history
    # feature's override-edit guard now rejects a PATCH against a
    # superseded plan_run with a 409, and `second`'s own generate call
    # created a fresh row for this vendor too (the frozen/override_locked
    # row is still a real, newly-persisted PlanAllocation).
    second_detail_by_vendor = {d["vendor_id"]: d for d in second["weekly_view"]["detail"]}
    new_override_amount = min(override_amount * 0.5, float(vendor["opening_balance"]))
    seeded_client.patch(
        f"/plan-allocations/{second_detail_by_vendor[vendor_id]['plan_allocation_id']}/override",
        json={"override_amount": new_override_amount},
    )

    third = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view", json={"available_funds": available_funds}
    ).json()
    assert third["total_overridden"] == pytest.approx(new_override_amount)
    assert third["funds_left_for_regeneration"] == pytest.approx(available_funds - new_override_amount)

    third_allocations = {a["vendor_id"]: a for a in third["plan"]["allocations"]}
    third_overridden_row = third_allocations[vendor_id]
    assert third_overridden_row["status"] == "override_locked"
    assert third_overridden_row["allocated_amount"] == pytest.approx(suggested_before_override, abs=1)


def test_new_model_2_override_alone_exceeding_funds_clamps_effective_funds_to_zero(seeded_client):
    """Edge case: the overridden vendor's amount alone exceeds available_funds
    -- effective_funds must clamp at 0, not go negative, and the existing
    escalation/escalation_shortfall machinery must reflect the shortfall
    naturally rather than erroring."""
    available_funds = 100_000.0

    first = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": available_funds, "planning_month": NM2_PLANNING_MONTH},
    ).json()
    detail = first["weekly_view"]["detail"]
    row = detail[0]
    vendor_id = row["vendor_id"]
    vendor = seeded_client.get(f"/vendors/{vendor_id}").json()

    override_amount = min(float(vendor["opening_balance"]) * 0.9, max(available_funds * 10, 1_000_000.0))
    assert override_amount > available_funds  # the whole point of this scenario
    seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount}
    )

    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view", json={"available_funds": available_funds}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["funds_left_for_regeneration"] == 0.0

    plan = body["plan"]
    assert plan["available_funds"] == 0.0
    if plan["guaranteed_total"] > 0:
        assert plan["escalation"] is True
        assert plan["escalation_shortfall"] == pytest.approx(plan["guaranteed_total"])
    assert all(a["allocated_amount"] >= 0 for a in plan["allocations"])


def test_week_distribution_plan_untouched_by_a_payment_in_a_different_week(seeded_client):
    """The concrete planned-Wx/paid-in-a-different-week scenario end to end:
    Finance's planning note for a vendor's week stays exactly as edited even
    after a real payment lands under a completely different week, and
    week_actual_paid correctly reflects that payment under its own week."""
    from backend.db.models import PlanAllocation, Vendor
    from backend.db.session import SessionLocal
    from backend.weekly_planning.calendar_utils import week_for_date, weeks_in_month

    row = _first_nm2_allocation_row(seeded_client)
    vendor_id = row["vendor_id"]

    # Force this vendor's assigned_week to a week STILL IN THE FUTURE
    # relative to today — not a hardcoded "2" — so the pulled-forward-week
    # fix (build_weekly_view(): a week that's already elapsed relative to
    # today gets pulled forward to today's real week) doesn't collapse this
    # into the same week the real payment below lands in, which would defeat
    # the whole "planned vs. paid in a different week" premise.
    today = date.today()
    current_week = week_for_date(today)
    target_week = min(current_week + 1, weeks_in_month(today.year, today.month))
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.assigned_week = target_week
        session.commit()
    finally:
        session.close()

    fresh = seeded_client.post("/models/5/generate-plan-and-weekly-view", json={"available_funds": 100_000}).json()
    fresh_row = next(r for r in fresh["weekly_view"]["detail"] if r["vendor_id"] == vendor_id)
    assert fresh_row["assigned_week"] == target_week
    planned_before = fresh_row["week_distribution_plan"]
    assert planned_before == {str(target_week): pytest.approx(fresh_row["actual_planned"])}
    assert fresh_row["week_actual_paid"] == {}
    plan_allocation_id = fresh_row["plan_allocation_id"]

    # Finance logs a real payment today — server-determined date/week
    # (contract from an earlier task), which lands wherever "today" falls,
    # not target_week.
    pay_resp = seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": 1000.0})
    assert pay_resp.status_code == 200

    # Re-group the SAME already-generated plan through the standalone
    # weekly-view endpoint with persist=False, purely to get a fresh
    # week_actual_paid read — deliberately NOT calling generate-plan again,
    # since a fresh Generate Plan creates a brand-new PlanAllocation row
    # with its own fresh default (and, now that required_amount is
    # live-payment-aware, a slightly different actual_planned too) — that
    # would prove nothing about whether THIS row's planned figure changed.
    replan = seeded_client.post(
        "/weekly-view", json={"vendor_allocations": fresh["plan"]["allocations"], "persist": False}
    ).json()
    replan_row = next(r for r in replan["detail"] if r["vendor_id"] == vendor_id)

    paid_week = next(iter(replan_row["week_actual_paid"]))
    assert replan_row["week_actual_paid"][paid_week] == pytest.approx(1000.0)
    assert paid_week != str(target_week), (
        "test setup coincidence: today's real week already IS the last week of the month, "
        "so no distinct future week exists — not a real cross-week proof"
    )

    # The ORIGINAL persisted row's planned figure is completely untouched by the payment.
    session = SessionLocal()
    try:
        allocation = session.query(PlanAllocation).filter_by(id=plan_allocation_id).one()
        assert allocation.week_distribution_plan == planned_before
    finally:
        session.close()


# ---- Weekly view (standalone) ---------------------------------------------


def test_standalone_weekly_view(seeded_client):
    plan = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": NM2_PLANNING_MONTH},
    ).json()["plan"]
    resp = seeded_client.post(
        "/weekly-view",
        json={"vendor_allocations": plan["allocations"], "model_used": "new_model_2", "funds_figure": 100_000, "persist": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_run_id"] is None  # persist=False
    assert "detail" in body and "weekly_summary" in body

# ---- Regeneration -----------------------------------------------------------
# The old /regenerate endpoint (eligibility-filtering/reconsider engine for
# Models 2/3) was removed once New Model 2 became the sole model — New
# Model 2 has no separate regeneration step at all (docs/14: the same
# generate-plan-and-weekly-view endpoint handles every "Regenerate" click,
# already covered above by test_plan_runs_history_treats_generate_and_regenerate_as_one_continuous_sequence).


# ---- Payments ---------------------------------------------------------------


def test_log_payment(seeded_client):
    """payment_date/week are no longer client-supplied — the server
    determines both itself (backend/shared/payment_logging.py::log_payment)."""
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": 1.0})
    assert resp.status_code == 200
    assert resp.json()["payment_status"] in ("not_paid", "partial", "paid_in_full")


def test_log_payment_ignores_client_supplied_date_and_week(seeded_client):
    """Contract: payment_date/week are computed server-side even if sent."""
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.post(
        "/payments",
        json={"vendor_id": vendor_id, "amount": 1.0, "payment_date": "2020-01-01", "week": 99},
    )
    assert resp.status_code == 200
    from backend.db.session import SessionLocal
    from backend.db.models import Payment

    session = SessionLocal()
    try:
        payment = session.query(Payment).filter_by(vendor_id=vendor_id).order_by(Payment.id.desc()).first()
        assert payment.payment_date == date.today()
        assert payment.week is not None and 1 <= payment.week <= 5
    finally:
        session.close()


def test_log_payment_overpayment_is_400(seeded_client):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    outstanding = seeded_client.get(f"/vendors/{vendor_id}").json()["opening_balance"]
    resp = seeded_client.post(
        "/payments",
        json={"vendor_id": vendor_id, "amount": outstanding + 1_000_000},
    )
    assert resp.status_code == 400
    assert "ledger-integrity violation" in resp.json()["detail"]


# ---- Vendor Payment Table (actual-payment tracking, this task) ------------


def test_vendor_payment_tracking_zero_before_any_payment(seeded_client):
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)
    resp = seeded_client.get("/vendors/payment-tracking")
    assert resp.status_code == 200
    row = next(r for r in resp.json() if r["vendor_id"] == vendor_id)
    assert row["outstanding"] == pytest.approx(1_200_000.0)
    assert row["actual_paid_this_month"] == 0.0
    assert row["balance_outstanding"] == pytest.approx(1_200_000.0)
    assert row["balance"] == pytest.approx(row["budget"])  # nothing paid yet -> balance == budget


def test_vendor_payment_tracking_min_funds_required_uses_v2_not_old_formula(seeded_client):
    """Confirmed real bug (2026-07-31 investigation, vendor V00400): this
    endpoint's min_funds_required was calling the OLD, retired-model-era
    required_amount() (backend/shared/min_funds.py) — for a Must Pay
    vendor that's just "last month's own payable" — instead of New Model
    2's required_amount_v2() (backend/shared/min_funds_v2.py), the figure
    the Planning table/weekly view already show. Constructed so the two
    formulas disagree: an older 500,000 tranche that's > 50% of the latest
    900,000 tranche makes v2 return the oldest tranche ALONE (500,000,
    rule v2_oldest_only), while the old Must-Pay branch would have
    returned just the latest month's raw payable (900,000).
    """
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal
    from backend.shared.enums import VendorCategory

    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.category = VendorCategory.MUST_PAY
        vendor.opening_balance = 1_400_000.0
        vendor.paid_so_far_this_month = 0.0
        session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).delete()
        session.add(
            MonthlyLedger(
                vendor_id=vendor_id, month=date(2025, 9, 1), payable=500_000.0, payment=0.0,
                opening_balance=0.0, closing_balance=500_000.0,
            )
        )
        session.add(
            MonthlyLedger(
                vendor_id=vendor_id, month=date(2026, 6, 1), payable=900_000.0, payment=0.0,
                opening_balance=500_000.0, closing_balance=1_400_000.0,
            )
        )
        session.commit()
    finally:
        session.close()

    row = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == vendor_id)
    assert row["min_funds_required"] == pytest.approx(500_000.0)  # v2_oldest_only, NOT 900,000


def test_vendor_payment_tracking_reflects_a_logged_payment(seeded_client):
    """The core scenario: log a real payment, confirm actual_paid_this_month
    (sourced from the real payments table via week_actual_paid, not the
    cached paid_so_far_this_month field), Balance, and Balance outstanding
    all update correctly and consistently against a hand-computed expectation."""
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)

    before = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == vendor_id)
    budget = before["budget"]

    payment = 400_000.0
    resp = seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": payment})
    assert resp.status_code == 200

    after = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == vendor_id)
    assert after["actual_paid_this_month"] == pytest.approx(payment, abs=1)
    assert after["outstanding"] == pytest.approx(1_200_000.0)  # unchanged — the cycle-start snapshot
    assert after["balance_outstanding"] == pytest.approx(1_200_000.0 - payment, abs=1)
    assert after["balance"] == pytest.approx(budget - payment, abs=1)

    # Independently cross-checked against the real payments table, not just
    # the endpoint's own internal consistency.
    from backend.db.models import Payment
    from backend.db.session import SessionLocal

    session = SessionLocal()
    try:
        real_total = sum(
            float(p.amount) for p in session.query(Payment).filter_by(vendor_id=vendor_id).all()
        )
    finally:
        session.close()
    assert after["actual_paid_this_month"] == pytest.approx(real_total, abs=1)


def test_vendor_payment_tracking_balance_outstanding_never_negative_even_if_overpaid_vs_budget(seeded_client):
    """Balance CAN go negative (paid more than Budget) but Balance
    outstanding must never go negative (ledger integrity) — the two are
    deliberately different guarantees."""
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)
    # Pay the full outstanding amount, comfortably likely to exceed whatever
    # tiny/zero Budget this vendor has (no plan generated this cycle here).
    resp = seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": 1_200_000.0})
    assert resp.status_code == 200

    row = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == vendor_id)
    assert row["balance_outstanding"] == pytest.approx(0.0, abs=1)
    assert row["balance_outstanding"] >= 0
    assert row["balance"] <= 0  # budget was 0 (no plan generated), paid 1.2M against it


def test_vendor_payment_tracking_budget_stays_zero_until_finalized(seeded_client):
    """Budget is a stable snapshot, not a live-following figure: generating
    a plan alone must NOT move it — only an explicit "Finalize Plan" click
    (POST /vendors/finalize-plan, model=5 for New Model 2) does."""
    row = _first_nm2_allocation_row(seeded_client, available_funds=5_000_000)

    before = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"])
    assert before["budget"] == 0.0  # plan generated, but never finalized yet

    resp = seeded_client.post("/vendors/finalize-plan", json={"model": 5})
    assert resp.status_code == 200
    assert resp.json()["vendor_count"] > 0

    after = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"])
    assert after["budget"] == pytest.approx(row["actual_planned"], abs=1)


def test_vendor_payment_tracking_within_week_order_reads_latest_new_model_2_plan_run(seeded_client):
    """Priority (planning table) reuses this same field, sourced from
    whichever plan_run is actually latest."""
    resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 5_000_000, "planning_month": NM2_PLANNING_MONTH},
    )
    assert resp.status_code == 200
    detail = resp.json()["weekly_view"]["detail"]
    assert detail
    row = detail[0]

    tracking = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"])
    assert tracking["within_week_order"] == row["within_week_order"]


def test_vendor_payment_tracking_budget_prefers_sticky_override_over_plan_run(seeded_client):
    row = _first_nm2_allocation_row(seeded_client)
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    override_amount = float(vendor["opening_balance"]) * 0.5
    seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": override_amount}
    )
    seeded_client.post("/vendors/finalize-plan", json={"model": 5})

    tracking = next(
        r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"]
    )
    assert tracking["budget"] == pytest.approx(override_amount)


def test_vendor_payment_tracking_budget_reflects_latest_override_across_repeated_finalizes(seeded_client):
    """The daily/weekly workflow this was built for: Finance overrides,
    finalizes, pays some vendors, then comes back and overrides again later
    — Budget must reflect the LATEST override at each Finalize click, not
    the first one."""
    row = _first_nm2_allocation_row(seeded_client)
    vendor = seeded_client.get(f"/vendors/{row['vendor_id']}").json()
    opening = float(vendor["opening_balance"])

    first_override = opening * 0.3
    seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": first_override}
    )
    seeded_client.post("/vendors/finalize-plan", json={"model": 5})
    tracking = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"])
    assert tracking["budget"] == pytest.approx(first_override)

    # A later override (simulating a different day/week) — Budget must not
    # move until Finalize is clicked again.
    second_override = opening * 0.6
    seeded_client.patch(
        f"/plan-allocations/{row['plan_allocation_id']}/override", json={"override_amount": second_override}
    )
    tracking = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"])
    assert tracking["budget"] == pytest.approx(first_override)  # unchanged — not finalized yet

    seeded_client.post("/vendors/finalize-plan", json={"model": 5})
    tracking = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == row["vendor_id"])
    assert tracking["budget"] == pytest.approx(second_override)  # now reflects the latest override


def test_vendor_payment_tracking_write_path_is_the_existing_payments_endpoint(seeded_client):
    """No new/duplicate write endpoint — the Pay action reuses POST
    /payments, whose existing overpayment guard already enforces
    "amount must not exceed Balance outstanding" (equivalent to
    opening_balance - paid_so_far_this_month, docs/07's ledger integrity
    rule) — confirms that guard rejects an amount exceeding
    balance_outstanding for this table's own row."""
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)
    row = next(r for r in seeded_client.get("/vendors/payment-tracking").json() if r["vendor_id"] == vendor_id)
    assert row["balance_outstanding"] == pytest.approx(1_200_000.0)

    resp = seeded_client.post(
        "/payments", json={"vendor_id": vendor_id, "amount": row["balance_outstanding"] + 1_000_000.0}
    )
    assert resp.status_code == 400
    assert "ledger-integrity violation" in resp.json()["detail"]

    # A valid amount, right at the edge of balance_outstanding, succeeds.
    resp = seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": row["balance_outstanding"]})
    assert resp.status_code == 200


# ---- Live outstanding amount + aging after a payment -----------------------


def _seed_known_balance_vendor(client, amount=1_200_000.0):
    """Overwrites one vendor's opening_balance + monthly_ledger with a
    single, clean tranche of a known amount — same MonthlyLedger-row
    construction test_vendor_edits.py already uses for constructed scenarios.

    Needed here because the real master Excel's Closing Balance/Total
    Payable columns are formulas whose cached values are currently blank
    (see this task's report) — a fresh ingestion of it (what seeded_client
    does) produces an all-zero opening_balance/ledger for every vendor,
    which can't exercise a partial/full payment scenario. This bypasses
    that pre-existing, unrelated data issue for a deterministic test.
    """
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal

    vendor_id = client.get("/vendors").json()[0]["id"]
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.opening_balance = amount
        vendor.paid_so_far_this_month = 0.0
        session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).delete()
        session.add(
            MonthlyLedger(
                vendor_id=vendor_id,
                month=date(2026, 5, 1),
                payable=amount,
                payment=0.0,
                opening_balance=0.0,
                closing_balance=amount,
            )
        )
        session.commit()
    finally:
        session.close()
    return vendor_id


def test_live_outstanding_balance_reflects_partial_then_full_payment(seeded_client):
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)
    before = seeded_client.get(f"/vendors/{vendor_id}").json()
    assert before["live_outstanding_balance"] == pytest.approx(1_200_000.0)  # nothing paid yet

    partial = 400_000.0
    seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": partial, "payment_date": str(date.today())})
    mid = seeded_client.get(f"/vendors/{vendor_id}").json()
    assert mid["live_outstanding_balance"] == pytest.approx(800_000.0, abs=1)
    assert mid["opening_balance"] == pytest.approx(1_200_000.0)  # unchanged — still means what it always meant

    seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": 800_000.0, "payment_date": str(date.today())})
    after = seeded_client.get(f"/vendors/{vendor_id}").json()
    assert after["live_outstanding_balance"] == pytest.approx(0, abs=1)  # paid in full, clamped at 0


def test_vendor_aging_reflects_a_logged_payment(seeded_client):
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)
    before = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    assert before["total_outstanding"] == pytest.approx(1_200_000.0, abs=1)
    # _seed_known_balance_vendor's single tranche is dated the same month as
    # the vendor's own latest ledger row (its as_of) -> 0 months back, "0-30".
    assert before["oldest_bucket"] == "0-30"
    assert before["oldest_bucket_months_back"] == 0

    seeded_client.post(
        "/payments", json={"vendor_id": vendor_id, "amount": 1_200_000.0, "payment_date": str(date.today())}
    )
    after = seeded_client.get(f"/vendors/{vendor_id}/aging").json()

    assert after["total_outstanding"] == pytest.approx(0, abs=1)
    assert after["oldest_bucket"] is None
    assert after["oldest_bucket_months_back"] is None


def test_vendor_aging_zero_outstanding_balance_degrades_gracefully(seeded_client):
    """Edge case for the vendor-payments summary table's percentage columns
    (frontend-computed from these fields, via formatPct()'s existing
    denominator > 0 guard): a vendor with zero outstanding balance at all has
    no oldest tranche — oldest_bucket_amount and oldest_bucket_amount_opening
    must both come back as a plain 0.0, not an error, so the frontend can
    degrade % outstanding paid / % oldest bill paid to "—" instead of
    dividing by zero.
    """
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=0.0)
    aging = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    assert aging["oldest_bucket"] is None
    assert aging["oldest_bucket_amount"] == 0.0
    assert aging["oldest_bucket_amount_opening"] == 0.0


def test_vendor_aging_exposes_real_months_back_for_a_severely_overdue_vendor(seeded_client):
    """A "120+" vendor must report its real months-back figure (7 here), not
    a flat display cap of 4 and not a value guessed from the bucket label."""
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal

    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.opening_balance = 500_000.0
        vendor.paid_so_far_this_month = 0.0
        session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).delete()
        session.add(
            MonthlyLedger(
                vendor_id=vendor_id,
                month=date(2025, 10, 1),  # 7 months before the vendor's own latest ledger row below
                payable=500_000.0,
                payment=0.0,
                opening_balance=0.0,
                closing_balance=500_000.0,
            )
        )
        session.add(
            MonthlyLedger(
                vendor_id=vendor_id,
                month=date(2026, 5, 1),
                payable=0.0,
                payment=0.0,
                opening_balance=0.0,
                closing_balance=500_000.0,
            )
        )
        session.commit()
    finally:
        session.close()

    aging = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    assert aging["oldest_bucket"] == "120+"
    assert aging["oldest_bucket_months_back"] == 7


def test_vendor_aging_exposes_oldest_bucket_amount_and_monthly_breakdown(seeded_client):
    """docs/04 redefinition: oldest_bucket_amount is just the oldest tranche
    (300,000 at 7 months back), not bucket_balances["120+"]'s lumped sum
    (which also includes the 200,000 at 5 months back) — and
    monthly_breakdown walks every calendar month oldest-first, including
    the zero-balance ones in between."""
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal

    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.opening_balance = 500_000.0
        vendor.paid_so_far_this_month = 0.0
        session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).delete()
        for month, payable in [
            (date(2025, 10, 1), 300_000.0),  # 7 months back
            (date(2025, 11, 1), 0.0),
            (date(2025, 12, 1), 200_000.0),  # 5 months back
            (date(2026, 1, 1), 0.0),
            (date(2026, 2, 1), 0.0),
            (date(2026, 3, 1), 0.0),
            (date(2026, 4, 1), 0.0),
            (date(2026, 5, 1), 0.0),  # as_of month
        ]:
            session.add(
                MonthlyLedger(
                    vendor_id=vendor_id,
                    month=month,
                    payable=payable,
                    payment=0.0,
                    opening_balance=0.0,
                    closing_balance=500_000.0,
                )
            )
        session.commit()
    finally:
        session.close()

    aging = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    assert aging["oldest_bucket"] == "120+"
    assert aging["bucket_balances"]["120+"] == pytest.approx(500_000.0)  # old lumped-sum, unchanged
    assert aging["oldest_bucket_amount"] == pytest.approx(300_000.0)  # new: just the oldest tranche

    breakdown = aging["monthly_breakdown"]
    assert [m["months_back"] for m in breakdown] == [7, 6, 5, 4, 3, 2, 1, 0]
    by_months_back = {m["months_back"]: m["amount"] for m in breakdown}
    assert by_months_back[7] == pytest.approx(300_000.0)
    assert by_months_back[5] == pytest.approx(200_000.0)
    for mb in (6, 4, 3, 2, 1, 0):
        assert by_months_back[mb] == 0.0


def test_vendor_aging_opening_vs_live_oldest_bucket_amount_isolates_fifo_spillover(seeded_client):
    """New vendor-payments-summary field: oldest_bucket_amount_opening is the
    same oldest-tranche figure but as it stood before any of this cycle's
    manual payments (always computed with already_paid_this_cycle=0.0), so
    it never moves — oldest_bucket_amount is the live one and moves with
    FIFO. Same 2-tranche setup as the test above (300,000 at 7 months back,
    200,000 at 5 months back), but this time a payment large enough to fully
    clear the oldest tranche AND spill into the next one — the sharpest case,
    since "oldest" itself shifts to a different tranche mid-cycle. Confirms
    the opening/live pair isn't a naive "total paid so far" subtraction: the
    two numbers refer to two different tranches once the original oldest one
    is fully cleared, and the gap between them is NOT the total payment.
    """
    from backend.db.models import MonthlyLedger, Vendor
    from backend.db.session import SessionLocal

    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        vendor.opening_balance = 500_000.0
        vendor.paid_so_far_this_month = 0.0
        session.query(MonthlyLedger).filter_by(vendor_id=vendor_id).delete()
        for month, payable in [
            (date(2025, 10, 1), 300_000.0),  # 7 months back — the oldest tranche
            (date(2025, 11, 1), 0.0),
            (date(2025, 12, 1), 200_000.0),  # 5 months back — next in line
            (date(2026, 1, 1), 0.0),
            (date(2026, 2, 1), 0.0),
            (date(2026, 3, 1), 0.0),
            (date(2026, 4, 1), 0.0),
            (date(2026, 5, 1), 0.0),  # as_of month
        ]:
            session.add(
                MonthlyLedger(
                    vendor_id=vendor_id,
                    month=month,
                    payable=payable,
                    payment=0.0,
                    opening_balance=0.0,
                    closing_balance=500_000.0,
                )
            )
        session.commit()
    finally:
        session.close()

    before = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    assert before["oldest_bucket_amount"] == pytest.approx(300_000.0)
    assert before["oldest_bucket_amount_opening"] == pytest.approx(300_000.0)  # nothing paid yet -> same as live

    payment = 400_000.0  # fully clears the 300,000 oldest tranche, 100,000 spills into the 200,000 one
    seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": payment, "payment_date": str(date.today())})

    after = seeded_client.get(f"/vendors/{vendor_id}/aging").json()
    # Opening never moves — always computed against a hypothetical zero paid
    # this cycle, regardless of what's actually been paid.
    assert after["oldest_bucket_amount_opening"] == pytest.approx(300_000.0)
    # Live figure now refers to the NEXT tranche (the original oldest is
    # fully cleared and excluded) — its remaining balance after 100,000 of
    # the payment spilled into it: 200,000 - 100,000 = 100,000.
    assert after["oldest_bucket_amount"] == pytest.approx(100_000.0)

    # The gap between opening and live is 200,000 — NOT the 400,000 actually
    # paid, and not the 300,000 that cleared the original oldest tranche
    # either: once "oldest" shifts tranches, opening and live are each
    # anchored to a DIFFERENT tranche, so their difference is a comparison
    # across tranches, not a single tranche's own before/after.
    assert after["oldest_bucket_amount_opening"] - after["oldest_bucket_amount"] == pytest.approx(200_000.0)
    assert after["oldest_bucket_amount_opening"] - after["oldest_bucket_amount"] != pytest.approx(payment)


def test_suggested_allocation_reflects_a_logged_payment(seeded_client):
    """Superseded expectation, updated: aging/required-amount are now
    live-payment-aware (bug fix — a payment logged mid-cycle must not be
    invisible to a later regeneration), so a fresh generate-plan call after
    a payment must show a required_amount reduced by exactly what was paid,
    not the old frozen pre-payment figure. allocated_amount/status stay the
    same here only because funds are so short (100,000 against a ~1.2M
    need) that a 1-rupee drop in required_amount doesn't move the vendor
    across the funds-shortfall cap either way — a previously-generated
    plan_allocation ROW already in the DB is still never retroactively
    rewritten; this is about what the NEXT generate-plan call computes.
    """
    vendor_id = _seed_known_balance_vendor(seeded_client, amount=1_200_000.0)
    before = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 100_000, "planning_month": NM2_PLANNING_MONTH},
    ).json()["plan"]
    before_row = next(a for a in before["allocations"] if a["vendor_id"] == vendor_id)

    payment = 1.0
    seeded_client.post("/payments", json={"vendor_id": vendor_id, "amount": payment})

    after = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view", json={"available_funds": 100_000}
    ).json()["plan"]
    after_row = next(a for a in after["allocations"] if a["vendor_id"] == vendor_id)

    assert after_row["required_amount"] == pytest.approx(before_row["required_amount"] - payment, abs=1e-6)
    # New Model 2's bucket-ceiling search is a global optimization across
    # every Normal/Inactive vendor's required_amount at once (unlike a
    # simple per-vendor cutoff) -- a ~₹1 change in this vendor's own
    # required_amount can shift the shared percentage search by a
    # fraction of a rupee for everyone, itself included. abs=1 tolerance,
    # not exact equality, is the right bar here.
    assert after_row["allocated_amount"] == pytest.approx(before_row["allocated_amount"], abs=1)
    assert after_row["status"] == before_row["status"]


# ---- Configuration ------------------------------------------------------


def test_config_list(seeded_client):
    resp = seeded_client.get("/config")
    assert resp.status_code == 200


def test_config_priority_buckets_crud_round_trip(seeded_client):
    """New Model 2 priority-bucket structure (docs/11 Task 2 Part C) — the
    router-level wiring for add/edit/remove, on top of the already-covered
    backend/configuration/test_priority_bucket_edits.py unit tests."""
    resp = seeded_client.get("/config/priority-buckets")
    assert resp.status_code == 200
    keys = {b["bucket_key"] for b in resp.json()}
    assert {"P2", "P3", "P4", "P5"} <= keys

    resp = seeded_client.post(
        "/config/priority-buckets",
        json={"bucket_key": "P6", "display_label": "P6", "ceiling_pct": 0.70, "floor_pct": 0.15, "rotation_position": 4},
    )
    assert resp.status_code == 200

    resp = seeded_client.put("/config/priority-buckets/P6", json={"ceiling_pct": 0.65})
    assert resp.status_code == 200
    assert resp.json()["changed"] is True

    resp = seeded_client.delete("/config/priority-buckets/P6")
    assert resp.status_code == 200

    resp = seeded_client.get("/config/priority-buckets")
    assert "P6" not in {b["bucket_key"] for b in resp.json()}


def test_config_priority_bucket_remove_blocked_when_vendor_tagged(seeded_client, tmp_path):
    patch_excel = str(tmp_path / "priority_bucket_patch.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    vendors = seeded_client.get("/vendors").json()
    normal_vendor = next(v for v in vendors if v["category"] == "normal")
    resp = seeded_client.patch(
        f"/vendors/{normal_vendor['id']}",
        json={"field": "priority_tag", "new_value": "P3", "excel_path": patch_excel},
    )
    assert resp.status_code == 200

    resp = seeded_client.delete("/config/priority-buckets/P3")
    assert resp.status_code == 400


# ---- Audit log ---------------------------------------------------------


def test_audit_log_list(seeded_client, tmp_path):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    patch_excel = str(tmp_path / "audit_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    seeded_client.patch(
        f"/vendors/{vendor_id}", json={"field": "commitment_months", "new_value": 2, "excel_path": patch_excel}
    )

    resp = seeded_client.get("/audit-log")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    assert len(resp.json()["items"]) >= 1


def test_audit_log_includes_vendor_name_and_erp_code(seeded_client, tmp_path):
    vendor = seeded_client.get("/vendors").json()[0]
    patch_excel = str(tmp_path / "audit_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    seeded_client.patch(
        f"/vendors/{vendor['id']}", json={"field": "commitment_months", "new_value": 2, "excel_path": patch_excel}
    )

    items = seeded_client.get("/audit-log", params={"vendor_id": vendor["id"]}).json()["items"]
    assert any(
        row["vendor_name"] == vendor["vendor_name"] and row["erp_code"] == vendor["erp_code"] for row in items
    )


def test_audit_log_search_matches_vendor_name_or_erp_code(seeded_client, tmp_path):
    vendor = seeded_client.get("/vendors").json()[0]
    patch_excel = str(tmp_path / "audit_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    seeded_client.patch(
        f"/vendors/{vendor['id']}", json={"field": "commitment_months", "new_value": 2, "excel_path": patch_excel}
    )

    by_name = seeded_client.get("/audit-log", params={"search": vendor["vendor_name"][:4]}).json()["items"]
    assert any(row["vendor_id"] == vendor["id"] for row in by_name)

    by_erp = seeded_client.get("/audit-log", params={"search": vendor["erp_code"]}).json()["items"]
    assert any(row["vendor_id"] == vendor["id"] for row in by_erp)

    no_match = seeded_client.get("/audit-log", params={"search": "no-such-vendor-xyz"}).json()["items"]
    assert no_match == []


def test_audit_log_source_filter(seeded_client, tmp_path):
    vendor = seeded_client.get("/vendors").json()[0]
    patch_excel = str(tmp_path / "audit_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    seeded_client.patch(
        f"/vendors/{vendor['id']}", json={"field": "commitment_months", "new_value": 2, "excel_path": patch_excel}
    )

    ui_edits = seeded_client.get("/audit-log", params={"source": "ui_edit"}).json()["items"]
    assert all(row["source"] == "ui_edit" for row in ui_edits)
    assert any(row["vendor_id"] == vendor["id"] for row in ui_edits)

    none_of_this_source = seeded_client.get("/audit-log", params={"source": "excel_upload"}).json()["items"]
    assert all(row["vendor_id"] != vendor["id"] for row in none_of_this_source)


def test_audit_log_date_range_filter(seeded_client, tmp_path):
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    patch_excel = str(tmp_path / "audit_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    seeded_client.patch(
        f"/vendors/{vendor_id}", json={"field": "commitment_months", "new_value": 2, "excel_path": patch_excel}
    )

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    future = (date.today() + timedelta(days=2)).isoformat()

    in_range = seeded_client.get("/audit-log", params={"date_from": today, "date_to": tomorrow}).json()
    assert in_range["total"] >= 1

    out_of_range = seeded_client.get("/audit-log", params={"date_from": tomorrow, "date_to": future}).json()
    assert out_of_range["total"] == 0
    assert out_of_range["items"] == []


def test_audit_log_pagination(seeded_client, tmp_path):
    vendor = seeded_client.get("/vendors").json()[0]
    patch_excel = str(tmp_path / "audit_copy.xlsx")
    shutil.copy(EXCEL_PATH, patch_excel)
    for months in (2, 3, 4):
        seeded_client.patch(
            f"/vendors/{vendor['id']}", json={"field": "commitment_months", "new_value": months, "excel_path": patch_excel}
        )

    full = seeded_client.get("/audit-log", params={"vendor_id": vendor["id"]}).json()
    assert full["total"] >= 3

    page1 = seeded_client.get("/audit-log", params={"vendor_id": vendor["id"], "limit": 2, "offset": 0}).json()
    page2 = seeded_client.get("/audit-log", params={"vendor_id": vendor["id"], "limit": 2, "offset": 2}).json()
    assert len(page1["items"]) == 2
    assert page1["total"] == full["total"] == page2["total"]
    assert [r["id"] for r in page1["items"]] != [r["id"] for r in page2["items"]]
    assert page1["items"][0]["id"] not in [r["id"] for r in page2["items"]]


# ---- Ops: ingestion (temp DB/Excel only) -------------------------------


def test_ingestion_load_end_to_end(client, excel_copy):
    resp = client.post("/ingestion/load", json={"excel_path": excel_copy})
    assert resp.status_code == 200
    body = resp.json()
    assert body["vendor_count"] == 83
    assert body["ledger_row_count"] == 83 * 14
