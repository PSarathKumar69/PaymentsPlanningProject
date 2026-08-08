"""Safety net for this directory + backend/api/ (this task's own conftest.py,
same content): resolve_column_mapping() now calls the real AI layer on EVERY
upload, not just when something's missing (see ai_column_mapper.py's module
docstring) — so any test that exercises commit_upload() (directly, or
through the /master-data/commit-upload endpoint) without its own mock would
otherwise silently make a REAL, live Gemini API call. Most of those tests
(test_upload.py and friends) predate this change and were never written to
mock it, because the old code never called Gemini at all when a sheet was
already fully resolved.

Scoped to backend/ingestion/ and backend/api/ only (not the whole suite) —
backend/ai_layer/test_talking_script.py deliberately tests gemini_client's
REAL _check_api_key() failure behavior (missing/placeholder key raises a
clear error); a suite-wide autouse patch of generate_text would swallow
that check before it ever runs and silently break those tests.

Patches backend.ai_layer.gemini_client directly (not
backend.ingestion.ai_column_mapper's imported reference to it) — several
test files (test_upload.py's _fresh_db_and_master(), and this file's own
copies of that pattern) pop backend.ingestion.ai_column_mapper from
sys.modules and re-import it fresh mid-test to force a clean module state;
a patch applied through that alias would be silently lost the moment the
module gets re-imported. backend.ai_layer.gemini_client is never on any of
those pop lists, so patching it directly survives every re-import.

Default stub: confirms whatever's already mapped, proposes nothing new —
equivalent to the AI simply agreeing with the deterministic pass every
time, so every pre-existing test's behavior is unaffected. Any test that
sets its own mock via monkeypatch.setattr(ai_column_mapper.gemini_client,
"generate_text", ...) — including every test in test_ai_column_mapper.py —
is unaffected either way, since that's the exact same underlying module
object; its own patch simply applies after this default and wins.

The ONLY place the real Gemini API is ever meant to run is the standalone
manual script (backend/ingestion/verify_ai_first_mapping_manually.py),
never pytest.
"""
import json

import pytest


@pytest.fixture(autouse=True)
def _stub_gemini_by_default(monkeypatch):
    from backend.ai_layer import gemini_client

    monkeypatch.setattr(
        gemini_client,
        "generate_text",
        lambda prompt: json.dumps({"mappings": {}, "warnings": []}),
    )
