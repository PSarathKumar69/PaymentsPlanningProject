"""Standalone, one-off manual verification — NOT collected by pytest, never
run in CI. Calls resolve_column_mapping() for REAL, against the real
Gemini API (the actual GEMINI_API_KEY already in .env), for both messy test
sheets in test_fixtures/. This is the real proof that the AI can actually
identify every field correctly on a scrambled, renamed sheet — the mocked
pytest tests in test_ai_column_mapper.py only prove the CODE applies a
scripted AI answer correctly, they say nothing about whether the real model
gets the answer right in the first place.

Uses a disposable, throwaway SQLite DB (never the real app.db) and always
rolls back — running this script never persists anything and never touches
real data.

Run: python -m backend.ingestion.verify_ai_first_mapping_manually
"""
import os
import tempfile

from dotenv import load_dotenv

load_dotenv()  # main.py normally does this — this script runs standalone, so it must too
os.environ.setdefault("PAYMENTS_DB_PATH", os.path.join(tempfile.mkdtemp(), "verify_ai_mapping.db"))

import openpyxl

from backend.ai_layer import gemini_client
from backend.db.session import SessionLocal
from backend.ingestion import ai_column_mapper, column_mapping

FIXTURES = [
    "backend/ingestion/test_fixtures/messy_sheet_1.xlsx",
    "backend/ingestion/test_fixtures/messy_sheet_2.xlsx",
]


def verify(path):
    print(f"\n{'=' * 78}\n{path}\n{'=' * 78}")
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    # Spy on the real generate_text (still calls the real API exactly once)
    # so this script can print the AI's raw per-field answer AND the final
    # resolved state from the SAME single real call, never two separate
    # (potentially different) calls.
    captured = {}
    real_generate_text = gemini_client.generate_text

    def spy(prompt):
        captured["prompt"] = prompt
        text = real_generate_text(prompt)
        captured["response"] = text
        return text

    gemini_client.generate_text = spy
    try:
        session = SessionLocal()
        try:
            result = ai_column_mapper.resolve_column_mapping(session, ws)
        finally:
            session.rollback()  # read-only verification — never persist an override
            session.close()
    finally:
        gemini_client.generate_text = real_generate_text

    raw = ai_column_mapper._parse_response(captured["response"])
    overrides = result["header_overrides"]

    detection = column_mapping.detect_header_row(ws)
    print(
        f"\nHeader row: deterministic candidate=row {detection.row_index} "
        f"(score={detection.score:.2f}, confident={detection.confident}) "
        f"-> final resolved={result['header_row']} (data starts row {result['data_start_row']})"
    )
    print(f"AI's own header_row answer: {raw.get('header_row')}")

    print("\nRaw AI answer per field (what the real model actually said):")
    for field in column_mapping.ALL_MAPPABLE_FIELDS:
        m = raw["mappings"].get(field) or {}
        marker = " <==" if field in ("priority_tag", "assigned_week") else ""
        print(
            f"  {field:20s} AI column={str(m.get('column')):22s} "
            f"confidence={str(m.get('confidence')):8s} reason={m.get('reason', '(no opinion given)')}{marker}"
        )
    if raw["warnings"]:
        print("\nAI warnings:")
        for w in raw["warnings"]:
            print(f"  - {w}")

    print("\nFinal resolved header per field (after this task's precedence rules):")
    for field in column_mapping.ALL_MAPPABLE_FIELDS:
        marker = " <==" if field in ("priority_tag", "assigned_week") else ""
        print(f"  {field:20s} -> {overrides.get(field)!r}{marker}")

    print("\nBanner messages (what Finance would actually see):")
    for msg in result["banner_messages"]:
        print(f"  - {msg}")
    if not result["banner_messages"]:
        print("  (none)")

    same_column = overrides.get("priority_tag") is not None and overrides.get("priority_tag") == overrides.get(
        "assigned_week"
    )
    print(
        f"\npriority_tag and assigned_week landed on the SAME column: {same_column}  "
        f"(must be False for correct disambiguation)"
    )
    print(f"priority_tag -> {overrides.get('priority_tag')!r}")
    print(f"assigned_week -> {overrides.get('assigned_week')!r}")


if __name__ == "__main__":
    for fixture_path in FIXTURES:
        verify(fixture_path)
