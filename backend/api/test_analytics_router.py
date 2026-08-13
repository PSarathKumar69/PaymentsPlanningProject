"""Router-level smoke tests for the Analytics dashboard endpoints
(backend/api/routers/analytics.py) — the router is thin wiring only, so
these just confirm the endpoints are reachable and shaped correctly; the
real math is covered by backend/analytics/test_calculations.py. Same
fresh-temp-DB isolation convention as test_new_model_2_router.py (duplicated
per this project's existing per-file-fixture convention)."""
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
    """Login-credential task: every route below is now behind
    get_current_user (backend/api/main.py) — this fixture logs in a
    throwaway test user so every existing test keeps hitting the real
    protected routes exactly as before, instead of each test needing its
    own login call."""
    app = _fresh_app(tmp_path)
    from backend.auth.security import create_user
    from backend.db.session import SessionLocal

    seed_session = SessionLocal()
    try:
        create_user(seed_session, "test_user", "test_password_123")
    finally:
        seed_session.close()

    test_client = TestClient(app)
    login = test_client.post("/auth/login", json={"username": "test_user", "password": "test_password_123"})
    assert login.status_code == 200, login.text
    return test_client


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


def test_dashboard_endpoint_end_to_end(seeded_client):
    resp = seeded_client.get("/analytics/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["vendors"]
    assert data["months"]
    assert len(data["aggregates"]) == len(data["months"])
    assert set(data["aging_totals"].keys()) == {"0-30", "31-60", "61-90", "91-120", "120+"}


def test_dashboard_endpoint_before_any_ingestion_is_empty_not_an_error(client):
    resp = client.get("/analytics/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["vendors"] == []
    assert data["months"] == []


def test_funds_trend_endpoint_empty_before_any_generate_plan(seeded_client):
    resp = seeded_client.get("/analytics/funds-trend")
    assert resp.status_code == 200
    assert resp.json() == {"trend": []}


def test_funds_trend_endpoint_reflects_a_real_generate_plan_run(seeded_client):
    generate = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert generate.status_code == 200

    resp = seeded_client.get("/analytics/funds-trend")
    assert resp.status_code == 200
    trend = resp.json()["trend"]
    assert len(trend) == 1
    assert trend[0]["month"] == "2026-08-01"
    assert trend[0]["available_funds"] == pytest.approx(1_000_000_000)
    assert trend[0]["min_funds_required"] > 0


def test_export_endpoint_returns_an_xlsx(seeded_client):
    resp = seeded_client.get("/analytics/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
