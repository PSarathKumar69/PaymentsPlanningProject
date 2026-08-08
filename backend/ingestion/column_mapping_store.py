"""Persistence for AI-resolved column-header mappings (data-pipeline
AI-mapping task) — thin CRUD over ColumnMappingOverride, kept separate from
both column_mapping.py (pure text/openpyxl logic, no DB) and
ai_column_mapper.py (the actual Gemini call) so each module has exactly one
job (CLAUDE.md rule 3's separation spirit, applied to this mapping-storage
concern too).

Also the DB-backed home for the sheet's configured start month (P1
demo-readiness task, CLAUDE.md rule 7) — same "small ingestion-config value,
not a column mapping override" reasoning that keeps it out of
column_mapping.py (no DB access there) but it's the closest existing
DB-persistence module for this kind of ingestion setting.
"""
from datetime import date, datetime

from backend.db.models import Config, ColumnMappingOverride
from backend.ingestion.column_mapping import DEFAULT_SHEET_START_MONTH

SHEET_START_MONTH_CONFIG_KEY = "ingestion.sheet_start_month"


def get_sheet_start_month(session):
    """The sheet's configured first Payable/Payment month — seeds today's
    DEFAULT_SHEET_START_MONTH into `config` on first read (same seed-on-read
    pattern backend/shared/scoring.py's weights use), never a bare constant
    referenced directly by callers that need the live value."""
    row = session.query(Config).filter_by(key=SHEET_START_MONTH_CONFIG_KEY).first()
    if row is None:
        row = Config(
            key=SHEET_START_MONTH_CONFIG_KEY,
            value=DEFAULT_SHEET_START_MONTH.isoformat(),
            description="First calendar month of the sheet's monthly Payable/Payment column block — "
            "every other month's identity is derived by position counting forward from this date "
            "(month header labels aren't reliably parseable, see column_mapping.py).",
        )
        session.add(row)
        session.flush()
    return date.fromisoformat(row.value)


def set_sheet_start_month(session, value):
    """Upsert, does not commit — caller owns the transaction (same
    convention as save_override below). `value`: a date; only year/month
    matter, always normalized to the 1st."""
    normalized = date(value.year, value.month, 1)
    row = session.query(Config).filter_by(key=SHEET_START_MONTH_CONFIG_KEY).first()
    if row is None:
        session.add(Config(key=SHEET_START_MONTH_CONFIG_KEY, value=normalized.isoformat()))
    else:
        row.value = normalized.isoformat()


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
