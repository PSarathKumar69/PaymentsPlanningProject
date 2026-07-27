"""Persistence for AI-resolved column-header mappings (data-pipeline
AI-mapping task) — thin CRUD over ColumnMappingOverride, kept separate from
both column_mapping.py (pure text/openpyxl logic, no DB) and
ai_column_mapper.py (the actual Gemini call) so each module has exactly one
job (CLAUDE.md rule 3's separation spirit, applied to this mapping-storage
concern too).
"""
from datetime import datetime

from backend.db.models import ColumnMappingOverride


def load_overrides(session):
    """{logical_field: header_text} for every field the AI (or, later, a
    config UI) has ever resolved — consulted by column_mapping.resolve_header()
    before falling back to a field's fixed default header text."""
    return {row.logical_field: row.header_text for row in session.query(ColumnMappingOverride).all()}


def save_override(session, logical_field, header_text, set_by="ai"):
    """Upsert — a later resolution for the same field replaces the earlier
    one outright (this is "what header means what right now", not a
    history; the audit_log is the history). Does not commit — caller owns
    the transaction, same convention as every other write in this package."""
    row = session.query(ColumnMappingOverride).filter_by(logical_field=logical_field).first()
    if row is None:
        row = ColumnMappingOverride(
            logical_field=logical_field, header_text=header_text, set_by=set_by, set_at=datetime.now()
        )
        session.add(row)
    else:
        row.header_text = header_text
        row.set_by = set_by
        row.set_at = datetime.now()
    return row
