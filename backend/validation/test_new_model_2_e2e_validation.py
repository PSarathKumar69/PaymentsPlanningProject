"""New Model 2 end-to-end validation — synthetic 45-vendor dataset
(2026-08-03 task): predictions were hand-computed and independently
reimplemented (test_data/compute_predictions.py) BEFORE this file was ever
run against the real allocator/generate-plan endpoint — see
test_data/predictions_scenario_*.md for the frozen prediction snapshots and
test_data/comparison_report.md for the resolved diff.

Isolation (non-negotiable, same convention as
backend/api/test_new_model_2_export.py's _fresh_app/backend/conftest.py's
guardrail): every test here runs against a fresh tmp_path SQLite DB and a
tmp_path copy of the master Excel. NEVER touches backend/db/app.db or
data/Vendor's Details.xlsx — backend/conftest.py's autouse fixture raises
immediately if that ever slips.

Re-run this file any time the allocator/min_funds_v2/planner changes, to
re-validate against these frozen, hand-verified numbers:
    python -m pytest backend/validation/test_new_model_2_e2e_validation.py -v
"""
import os
import shutil
import sys

import pytest
from fastapi.testclient import TestClient

from test_data.build_dummy_workbook import build_workbook
from test_data.compute_predictions import allocate, compute_rows
from test_data.dataset_spec import AS_OF_MONTH_ISO, PLANNING_MONTH, SHEET_START_MONTH_ISO, VENDORS

MONEY_TOL = 1.0  # rupee tolerance for float-rounding drift, not a real discrepancy

# Branch-F vendors (fully paid off, zero outstanding balance) — a real,
# documented behavior (docs/06 "a vendor with nothing left to pay must never
# re-enter scoring/ranking/allocation/eligibility", cross-referenced by
# docs/14): backend/shared/min_funds.py::_load_vendors_and_ledger()'s
# has_outstanding_balance() filter drops these from generate_plan() and
# build_weekly_view() ENTIRELY, not just to a zero allocation. Mispredicted
# in this task's first pass (assumed they'd still appear at Rs 0) — see
# test_data/comparison_report.md.
ZERO_OUTSTANDING_ERP_CODES = {"MP006", "NM2P2-06", "NM2P3-05", "NM2P4-06", "NM2P5-05"}


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
def dummy_excel_copy(tmp_path):
    from test_data.build_dummy_workbook import OUTPUT_PATH

    if not os.path.exists(OUTPUT_PATH):
        build_workbook()
    dest = tmp_path / "dummy_vendors_45_copy.xlsx"
    shutil.copy(OUTPUT_PATH, dest)
    return str(dest)


@pytest.fixture
def seeded_client(client, dummy_excel_copy):
    """Real ingestion pipeline (load()), against the isolated DB only —
    never EXCEL_PATH/app.db (see module docstring)."""
    from backend.db.session import SessionLocal
    from backend.ingestion import column_mapping_store
    from datetime import date

    session = SessionLocal()
    column_mapping_store.set_sheet_start_month(session, date.fromisoformat(SHEET_START_MONTH_ISO))
    session.commit()
    session.close()

    resp = client.post("/ingestion/load", json={"excel_path": dummy_excel_copy})
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["vendor_count"] == 45
    assert report["deactivated_vendor_count"] == 0
    return client


def test_never_touches_real_db_or_excel(seeded_client):
    """Provably safe: the real files' bytes/mtime must be untouched no
    matter what this whole test module does."""
    from backend.ingestion.load_excel import EXCEL_PATH

    assert os.path.exists(EXCEL_PATH), "real master excel should still exist, untouched"
    real_db_path = os.path.join(os.path.dirname(__file__), "..", "db", "app.db")
    # Not asserting content here (other test suites may touch it) — the
    # real guarantee is backend/conftest.py's autouse guardrail, which
    # raises immediately on any real commit/overwrite. This test only
    # documents the expectation.
    assert os.environ["PAYMENTS_DB_PATH"] != os.path.normpath(real_db_path)


def test_ingestion_derives_category_and_priority_tag_consistently(seeded_client):
    from backend.db.session import SessionLocal
    from backend.db.models import Vendor

    session = SessionLocal()
    try:
        vendors = {v.erp_code: v for v in session.query(Vendor).all()}
        assert len(vendors) == 45
        for spec in VENDORS:
            v = vendors[spec.erp_code]
            assert v.priority_tag == spec.priority_tag, f"{spec.erp_code}: priority_tag"
            expected_category = {
                "Must Pay": "must_pay",
                "Commitment": "commitment",
                "Normal": "normal",
                "Inactive": "inactive",
            }[spec.category_label]
            assert v.category == expected_category, f"{spec.erp_code}: category"
    finally:
        session.close()


def test_priority_buckets_match_documented_defaults(seeded_client):
    """docs/14's starting ceiling/floor values — read fresh from the DB
    (seeded on first generate_plan() call) rather than assumed, per the
    task's own instruction to verify what's actually configured. Includes
    P0/P1 (P0/P1-ceiling task) — pinned rows, seeded at 100%/100% so they
    change nothing until Finance explicitly edits them."""
    seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1.0, "planning_month": PLANNING_MONTH},
    )
    from backend.db.session import SessionLocal
    from backend.db.models import PriorityBucket

    session = SessionLocal()
    try:
        rows = {r.bucket_key: (float(r.ceiling_pct), float(r.floor_pct)) for r in session.query(PriorityBucket).all()}
        assert rows == {
            "P0": (1.0, 1.0),
            "P1": (1.0, 1.0),
            "P2": (0.95, 0.25),
            "P3": (0.90, 0.25),
            "P4": (0.85, 0.25),
            "P5": (0.50, 0.10),
        }, f"Configured bucket ceilings/floors differ from docs/14's starting values: {rows}"
    finally:
        session.close()


SCENARIOS = [
    ("fully_funded", 9_000_000),
    ("moderate_shortfall", 7_500_000),
    ("severe_shortfall", 4_000_000),
]


def _run_scenario(client, available_funds):
    resp = client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": available_funds, "planning_month": PLANNING_MONTH},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize("name,available_funds", SCENARIOS)
def test_scenario_matches_prediction(seeded_client, name, available_funds):
    predicted_rows = compute_rows()
    predicted = allocate(available_funds, predicted_rows)

    actual = _run_scenario(seeded_client, available_funds)
    plan = actual["plan"]

    assert plan["guaranteed_total"] == pytest.approx(predicted["guaranteed_total"], abs=MONEY_TOL)
    assert plan["escalation"] == predicted["escalation"]
    assert plan["escalation_shortfall"] == pytest.approx(predicted["escalation_shortfall"], abs=MONEY_TOL)
    assert plan["exceptional_shortfall"] == predicted["exceptional_shortfall"]
    for bucket, pct in predicted["bucket_pct"].items():
        assert plan["bucket_pct"][bucket] == pytest.approx(pct, abs=1e-9), f"{name}: bucket_pct[{bucket}]"
    assert plan["leftover_topup_total"] == pytest.approx(predicted["leftover_topup_total"], abs=MONEY_TOL)
    assert plan["leftover_remaining"] == pytest.approx(predicted["leftover_remaining"], abs=MONEY_TOL)

    actual_by_erp = {row["erp_code"]: row for row in plan["allocations"]}
    expected_erp_codes = {v.erp_code for v in VENDORS} - ZERO_OUTSTANDING_ERP_CODES
    assert set(actual_by_erp) == expected_erp_codes, (
        "vendor set in the plan differs from expected — zero-outstanding-balance vendors "
        f"({ZERO_OUTSTANDING_ERP_CODES}) should be the only ones excluded"
    )
    mismatches = []
    for erp_code, exp in predicted["allocations"].items():
        act = actual_by_erp[erp_code]
        if act["allocated_amount"] != pytest.approx(exp["allocated_amount"], abs=MONEY_TOL):
            mismatches.append((erp_code, "allocated_amount", exp["allocated_amount"], act["allocated_amount"]))
        if act["status"] != exp["status"]:
            mismatches.append((erp_code, "status", exp["status"], act["status"]))
    assert not mismatches, f"{name}: predicted vs actual mismatches: {mismatches}"


def test_severe_shortfall_finalize_still_publishes_with_predicted_over_by(seeded_client):
    """Scenario 3 mirrors the real production over-shortfall case already
    seen live (Sarath's own data: P0+P1 alone exceeded funds) — the exact
    case that surfaced this test's own bug (2026-08-08 task).

    over_by must be the CUTTABLE shortfall — the same pinned-vs-Normal/
    Inactive split allocator.py's own leftover_remaining uses (funds are
    handed to the guaranteed P0/P1 tier first, uncapped; only what's left
    after that is available to Normal/Inactive) — NOT a flat "everything
    committed minus available funds" sum. A flat sum (the bug this test
    used to encode: guaranteed_total's own FULL required amount, added to
    the Normal/Inactive floor-allocated total, compared to available_funds
    = Rs 6,088,000 - Rs 4,000,000 = Rs 2,088,000) double-counts P0/P1's own
    inherent, un-fixable-by-cutting-Normal/Inactive shortfall as if
    Finance could act on it. The correct, cuttable figure is
    -leftover_remaining = Rs 798,000 (test_data/compute_predictions.py's
    own allocate() already computes this the same pinned-first way;
    independently reconfirmed via `python -c` against this exact scenario
    before writing this assertion).

    Finalize also no longer BLOCKS on this (Sarath's explicit call, same
    task) — CLAUDE.md rule 2: suggestion, never enforce. The Budget
    snapshot now publishes regardless of `ok` — proven below via
    vendor_count (finalize_plan() always returns the real vendor count now,
    never 0 for a shortfall). Not re-checked via the finalized-plan-export
    endpoint here: that endpoint reads EXCEL_PATH directly (the real
    master workbook), which this dummy 45-vendor dataset was never wired
    up against (only ever loaded via an explicit excel_path override on
    /ingestion/load) — a separate, pre-existing gap in this test file, out
    of scope for this task."""
    available_funds = 4_000_000
    _run_scenario(seeded_client, available_funds)

    predicted_rows = compute_rows()
    predicted = allocate(available_funds, predicted_rows)
    predicted_total_committed = predicted["guaranteed_total"] + sum(
        a["allocated_amount"] for erp, a in predicted["allocations"].items()
        if erp not in {v.erp_code for v in VENDORS if v.category_label in ("Must Pay", "Commitment")}
    )
    predicted_over_by = max(-predicted["leftover_remaining"], 0.0)
    assert predicted_over_by == pytest.approx(798_000, abs=MONEY_TOL)

    finalize = seeded_client.post("/models/5/finalize", json={})
    assert finalize.status_code == 200
    body = finalize.json()
    assert body["ok"] is False
    assert body["over_by"] == pytest.approx(predicted_over_by, abs=MONEY_TOL)
    assert body["total_committed"] == pytest.approx(predicted_total_committed, abs=MONEY_TOL)
    assert body["available_funds"] == pytest.approx(available_funds, abs=MONEY_TOL)
    assert body["vendor_count"] > 0, "Finalize must still publish the Budget snapshot despite the shortfall"


def test_weekly_view_placement_matches_assigned_week_no_splitting(seeded_client):
    actual = _run_scenario(seeded_client, 9_000_000)
    detail_by_erp = {row["erp_code"]: row for row in actual["weekly_view"]["detail"]}
    expected_vendors = [v for v in VENDORS if v.erp_code not in ZERO_OUTSTANDING_ERP_CODES]
    for spec in expected_vendors:
        row = detail_by_erp.get(spec.erp_code)
        assert row is not None, f"{spec.erp_code} missing from weekly view detail"
        # planning_month (2026-09) isn't today's real calendar month, so no
        # pull-forward should apply (planner.py) — the vendor's own
        # Assigned Week column value should show through unchanged.
        assert row["assigned_week"] == spec.assigned_week, f"{spec.erp_code}: weekly placement"
        # No splitting: exactly one row per vendor in the detail table.
    # Zero-outstanding-balance vendors (build_weekly_view() uses the same
    # has_outstanding_balance() filter as generate_plan() — see
    # ZERO_OUTSTANDING_ERP_CODES's own comment) are absent, not zero.
    for erp_code in ZERO_OUTSTANDING_ERP_CODES:
        assert erp_code not in detail_by_erp
    assert len(actual["weekly_view"]["detail"]) == len(expected_vendors)
