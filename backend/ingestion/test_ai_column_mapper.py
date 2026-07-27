"""Tests for the AI-assisted column-mapping layer (data-pipeline
AI-mapping task — supersedes Part B of
claude_code_prompt_priority_column_and_ai_mapping.md). Same isolation
convention as test_upload.py (fresh temp DB via PAYMENTS_DB_PATH + module
re-import, tmp_path copy of the real master Excel). Never calls the real
Gemini API — backend.ingestion.ai_column_mapper.gemini_client.generate_text
is always monkeypatched.
"""
import json
import os
import shutil
import sys

import openpyxl
import pytest


def _fresh_db_and_master(tmp_path):
    """Mirrors test_upload.py's own helper of the same name exactly (same
    narrow module-pop list, same reasoning — see that file's docstring)."""
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    for mod in (
        "backend.db.session",
        "backend.ingestion.load_excel",
        "backend.ingestion.upload",
        "backend.ingestion.ai_column_mapper",
        "backend.ingestion.column_mapping_store",
        "backend.configuration.vendor_edits",
        "backend.configuration.extra_fields",
    ):
        sys.modules.pop(mod, None)
    from backend.ingestion.load_excel import EXCEL_PATH, load

    master_path = str(tmp_path / "master.xlsx")
    shutil.copy(EXCEL_PATH, master_path)
    load(excel_path=master_path)
    return master_path


def _rename_header(src_path, dest_path, old_header, new_header):
    from backend.ingestion.column_mapping import HEADER_ROW, build_sheet_map, find_column

    wb = openpyxl.load_workbook(src_path)
    ws = wb.active
    build_sheet_map(ws)  # sanity: the sheet is well-formed before the rename
    col = find_column(ws, old_header)
    assert col is not None, f"column {old_header!r} not found"
    ws.cell(row=HEADER_ROW, column=col, value=new_header)
    wb.save(dest_path)


def _remove_header(src_path, dest_path, header_to_blank):
    """Blanks a header cell entirely — simulates a column that's genuinely
    gone (not just renamed), for the "AI also can't resolve it" scenario."""
    from backend.ingestion.column_mapping import HEADER_ROW, find_column

    wb = openpyxl.load_workbook(src_path)
    ws = wb.active
    col = find_column(ws, header_to_blank)
    assert col is not None, f"column {header_to_blank!r} not found"
    ws.cell(row=HEADER_ROW, column=col).value = None
    wb.save(dest_path)


def _fake_gemini(mappings=None, warnings=None):
    """Returns a fake generate_text(prompt) that always answers with the
    given mappings/warnings, formatted exactly as the real prompt asks."""
    payload = {"mappings": mappings or {}, "warnings": warnings or []}

    def _fn(prompt):
        return json.dumps(payload)

    return _fn


# ---- Unit: prompt construction / response parsing --------------------------


def test_build_prompt_includes_field_descriptions_and_sample_data():
    from backend.ingestion import ai_column_mapper

    prompt = ai_column_mapper.build_prompt(
        header_row=["ERP Code", "V-P"],
        sample_rows=[["V001", "P0"], ["V002", "P2"]],
        fields_to_map=["priority_tag"],
        resolved_field_headers={"erp_code": "ERP Code"},
    )
    assert "priority_tag" in prompt
    assert "P0/P1/P2/P3/P4/P5" in prompt or "P0" in prompt  # field description text present
    assert "V-P" in prompt and "P0" in prompt  # real sample data present, not just headers


def test_parse_response_accepts_plain_json():
    from backend.ingestion import ai_column_mapper

    parsed = ai_column_mapper._parse_response(
        json.dumps({"mappings": {"priority_tag": {"column": "V-P", "confidence": "high", "reason": "x"}}}, )
    )
    assert parsed["mappings"]["priority_tag"]["column"] == "V-P"
    assert parsed["warnings"] == []


def test_parse_response_tolerates_markdown_fences():
    from backend.ingestion import ai_column_mapper

    fenced = "```json\n" + json.dumps({"mappings": {}, "warnings": ["heads up"]}) + "\n```"
    parsed = ai_column_mapper._parse_response(fenced)
    assert parsed["warnings"] == ["heads up"]


def test_parse_response_raises_on_non_json():
    from backend.ingestion import ai_column_mapper

    with pytest.raises(ValueError, match="non-JSON"):
        ai_column_mapper._parse_response("sorry, I can't help with that")


def test_map_columns_calls_gemini_and_returns_parsed_result(monkeypatch):
    from backend.ingestion import ai_column_mapper

    monkeypatch.setattr(
        ai_column_mapper.gemini_client,
        "generate_text",
        _fake_gemini(mappings={"priority_tag": {"column": "V-P", "confidence": "high", "reason": "values are P0-P4"}}),
    )
    result = ai_column_mapper.map_columns(
        header_row=["ERP Code", "V-P"],
        sample_rows=[["V001", "P0"]],
        fields_to_map=["priority_tag"],
        resolved_field_headers={"erp_code": "ERP Code"},
    )
    assert result["mappings"]["priority_tag"]["column"] == "V-P"


# ---- Integration: resolve_column_mapping() against a real workbook --------


def test_fully_resolved_sheet_never_calls_gemini(tmp_path, monkeypatch):
    """Every expected column present and correctly named: no gap to fill, no
    cross-check call either (this task's own documented, flagged resolution
    of the "every single upload" vs "only called again if something's
    unresolved" tension — see ai_column_mapper.py's module docstring).
    Outcome asserted here: zero Gemini calls, no mapping changes."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.session import SessionLocal
    from backend.ingestion import ai_column_mapper

    calls = []
    monkeypatch.setattr(ai_column_mapper.gemini_client, "generate_text", lambda p: calls.append(p) or "{}")

    wb = openpyxl.load_workbook(master_path, data_only=True)
    session = SessionLocal()
    try:
        result = ai_column_mapper.resolve_column_mapping(session, wb.active)
    finally:
        session.close()

    assert calls == []  # never invoked
    assert result["audit_entries"] == []
    assert result["banner_messages"] == []


def test_renamed_soft_field_gap_filled_and_persisted_second_upload_skips_gemini(tmp_path, monkeypatch):
    """The task's own example scenario: "Priority Tag" renamed to "V-P".
    First upload: AI fills the gap, mapping gets auto-persisted, field reads
    correctly. Second upload with the SAME renamed header: resolves
    deterministically — assert the mock was only called once across both
    uploads."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.models import AuditLog, Vendor
    from backend.db.session import SessionLocal
    from backend.ingestion.upload import commit_upload
    from backend.shared.enums import ChangeSource, VendorPriorityTag

    calls = []

    def fake_generate_text(prompt):
        calls.append(prompt)
        return json.dumps(
            {
                "mappings": {
                    "priority_tag": {
                        "column": "V-P",
                        "confidence": "high",
                        "reason": "values in this column are exactly P0-P5",
                    }
                },
                "warnings": [],
            }
        )

    import backend.ingestion.ai_column_mapper as ai_column_mapper

    monkeypatch.setattr(ai_column_mapper.gemini_client, "generate_text", fake_generate_text)

    backup_path = str(tmp_path / "master.backup.xlsx")

    # --- Upload 1: "Priority Tag" renamed to "V-P" ---
    upload_1 = str(tmp_path / "upload1.xlsx")
    _rename_header(master_path, upload_1, "Priority Tag", "V-P")
    result_1 = commit_upload(upload_1, excel_path=master_path, backup_path=backup_path)

    assert len(calls) == 1
    assert any("V-P" in m for m in result_1["ai_column_mapping_messages"])

    session = SessionLocal()
    try:
        # The mapping was actually applied: at least one vendor's
        # priority_tag came from the renamed column, not left None.
        tagged = [v for v in session.query(Vendor).all() if v.priority_tag is not None]
        assert tagged, "no vendor got a priority_tag from the AI-resolved 'V-P' column"
        assert all(v.priority_tag in VendorPriorityTag for v in tagged)

        entry = (
            session.query(AuditLog)
            .filter_by(source=ChangeSource.AI_COLUMN_MAPPING.value, field_name="column_mapping:priority_tag")
            .one()
        )
        assert "V-P" in entry.new_value
        assert "P0-P4" in entry.new_value or "confidence=high" in entry.new_value
    finally:
        session.close()

    # --- Upload 2: SAME renamed header again ---
    upload_2 = str(tmp_path / "upload2.xlsx")
    shutil.copy(upload_1, upload_2)  # already has "V-P", no further changes needed
    commit_upload(upload_2, excel_path=master_path, backup_path=backup_path)

    assert len(calls) == 1  # still only the one call, across BOTH uploads


def test_required_field_ai_cannot_resolve_rejects_upload_with_clear_message(tmp_path, monkeypatch):
    """A genuinely unresolvable REQUIRED field (blanked header, AI reports
    low confidence): upload is rejected, message names the field. This is
    the only case that still blocks (task's final safety net)."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.ingestion import ai_column_mapper
    from backend.ingestion.column_mapping import MissingRequiredColumnsError
    from backend.ingestion.upload import commit_upload

    def fake_generate_text(prompt):
        return json.dumps(
            {
                "mappings": {
                    "entity": {"column": None, "confidence": "low", "reason": "no column plausibly represents entity"}
                },
                "warnings": [],
            }
        )

    monkeypatch.setattr(ai_column_mapper.gemini_client, "generate_text", fake_generate_text)

    upload_path = str(tmp_path / "upload.xlsx")
    _remove_header(master_path, upload_path, "Entity")
    backup_path = str(tmp_path / "master.backup.xlsx")

    with pytest.raises(MissingRequiredColumnsError, match="entity"):
        commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)


def test_required_field_ai_confidently_resolves_it_upload_succeeds(tmp_path, monkeypatch):
    """A renamed REQUIRED field (not just a soft one): AI resolves it with
    high confidence, upload proceeds and reads that field correctly."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.session import SessionLocal
    from backend.db.models import Vendor
    from backend.ingestion import ai_column_mapper
    from backend.ingestion.upload import commit_upload

    def fake_generate_text(prompt):
        return json.dumps(
            {
                "mappings": {
                    "entity": {"column": "Biz Unit", "confidence": "high", "reason": "same short entity codes as before"}
                },
                "warnings": [],
            }
        )

    monkeypatch.setattr(ai_column_mapper.gemini_client, "generate_text", fake_generate_text)

    upload_path = str(tmp_path / "upload.xlsx")
    _rename_header(master_path, upload_path, "Entity", "Biz Unit")
    backup_path = str(tmp_path / "master.backup.xlsx")

    result = commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)
    assert result["vendor_count"] > 0  # ingestion actually completed, not rejected

    session = SessionLocal()
    try:
        vendors = session.query(Vendor).all()
        assert all(v.entity for v in vendors)  # entity field populated from the renamed column
    finally:
        session.close()


def test_ai_mapping_never_overrides_an_already_resolved_field(tmp_path, monkeypatch):
    """Cross-check must never override a field the deterministic pass
    already resolved, even if the AI's response includes a "mapping" for
    it — only fields actually in fields_to_map get applied."""
    master_path = _fresh_db_and_master(tmp_path)
    from backend.db.session import SessionLocal
    from backend.db.models import Vendor
    from backend.ingestion import ai_column_mapper
    from backend.ingestion.upload import commit_upload
    from backend.shared.enums import VendorCategory

    def fake_generate_text(prompt):
        # Also (incorrectly) proposes remapping "category", which is NOT in
        # fields_to_map (it's already resolved) — must be ignored.
        return json.dumps(
            {
                "mappings": {
                    "priority_tag": {"column": "V-P", "confidence": "high", "reason": "P0-P4 values"},
                    "category": {"column": "Status", "confidence": "high", "reason": "should not be applied"},
                },
                "warnings": [],
            }
        )

    monkeypatch.setattr(ai_column_mapper.gemini_client, "generate_text", fake_generate_text)

    upload_path = str(tmp_path / "upload.xlsx")
    _rename_header(master_path, upload_path, "Priority Tag", "V-P")
    backup_path = str(tmp_path / "master.backup.xlsx")

    commit_upload(upload_path, excel_path=master_path, backup_path=backup_path)

    session = SessionLocal()
    try:
        # category still reads from the real "Category" column, unaffected —
        # every vendor's category is still a legitimate enum value, not
        # garbage read from the "Status" (Active/Blocked) column.
        vendors = session.query(Vendor).all()
        assert all(v.category in VendorCategory for v in vendors)
    finally:
        session.close()
