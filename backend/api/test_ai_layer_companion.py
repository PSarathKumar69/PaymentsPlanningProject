"""Router-level tests for New Model 2's vendor talking points companion —
POST /ai/vendor-talking-points. Same isolation convention as
backend/api/test_new_model_2_router.py (fresh temp DB per test, whole
backend.* import graph re-bound) — duplicated here rather than shared via
conftest.py, matching this project's existing per-file-fixture convention.

Never calls the real Gemini API — gemini_client.generate_text is
monkeypatched throughout.

The prior "broader Q&A" mode (POST /ai/ask) was removed in this task
(Sarath's scope correction: "this functionality is enough" — vendor talking
points only) — its tests are removed too, not just left unused.
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


def _mock_gemini(monkeypatch):
    # Imported fresh here, AFTER seeded_client's _fresh_app() has already
    # popped/re-imported every backend.* module for this test's own temp DB —
    # patching a stale pre-pop module object would silently do nothing (see
    # memory/demo15_dataset_quirks.md's documented sys.modules staleness trap).
    import backend.ai_layer.gemini_client as gemini_client

    monkeypatch.setattr(gemini_client, "generate_text", lambda prompt: f"MOCKED SCRIPT ({len(prompt)} chars)")


def test_vendor_talking_points_endpoint_end_to_end(seeded_client, monkeypatch):
    _mock_gemini(monkeypatch)
    generate = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1_000_000_000, "planning_month": "2026-08"},
    )
    assert generate.status_code == 200
    vendor_id = generate.json()["plan"]["allocations"][0]["vendor_id"]

    resp = seeded_client.post("/ai/vendor-talking-points", json={"vendor_id": vendor_id})
    assert resp.status_code == 200
    data = resp.json()
    assert data["vendor_id"] == vendor_id
    assert data["script_text"].startswith("MOCKED SCRIPT")


def test_vendor_talking_points_with_no_plan_yet_is_400(seeded_client, monkeypatch):
    _mock_gemini(monkeypatch)
    vendor_id = seeded_client.get("/vendors").json()[0]["id"]
    resp = seeded_client.post("/ai/vendor-talking-points", json={"vendor_id": vendor_id})
    assert resp.status_code == 400


def test_ask_endpoint_no_longer_exists(seeded_client):
    """Sarath's scope correction removed the broader Q&A path entirely —
    the route must be gone, not just unused."""
    resp = seeded_client.post("/ai/ask", json={"question": "anything"})
    assert resp.status_code == 404


# ---- Existing zero-only talking-script feature stays unaffected -----------


def test_existing_talking_scripts_endpoint_still_works(seeded_client, monkeypatch):
    _mock_gemini(monkeypatch)
    plan_resp = seeded_client.post(
        "/models/5/generate-plan-and-weekly-view",
        json={"available_funds": 1, "planning_month": "2026-08"},
    )
    assert plan_resp.status_code == 200
    allocations = plan_resp.json()["plan"]["allocations"]

    resp = seeded_client.post("/ai/talking-scripts", json={"vendor_allocations": allocations})
    assert resp.status_code == 200
    scripts = resp.json()["scripts"]
    assert all(s["script_text"].startswith("MOCKED SCRIPT") for s in scripts)
