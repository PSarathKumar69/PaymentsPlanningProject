"""Persistence for Finance's confirmed current-cycle planning month (Main-tab
upload-confirm task) — same Config-backed get/set pattern as
backend/ingestion/column_mapping_store.py's get_sheet_start_month()/
set_sheet_start_month(). Exists so a planning month is readable as soon as
Finance confirms one in the upload modal, before any New Model 2 plan_run
has ever been created this cycle — new_model_2.py's _resolve_planning_month()
falls back to this only after checking the latest plan_run.
"""
from backend.db.models import Config

PLANNING_MONTH_CONFIG_KEY = "planning.current_planning_month"


def get_current_planning_month(session):
    """"YYYY-MM", or None if Finance has never confirmed one yet (no upload
    has gone through the confirm modal, and no plan has ever been generated)."""
    row = session.query(Config).filter_by(key=PLANNING_MONTH_CONFIG_KEY).first()
    return row.value if row is not None else None


def set_current_planning_month(session, value):
    """Upsert, does not commit — caller owns the transaction (same
    convention as column_mapping_store.set_sheet_start_month). `value`:
    "YYYY-MM" string."""
    row = session.query(Config).filter_by(key=PLANNING_MONTH_CONFIG_KEY).first()
    if row is None:
        session.add(Config(key=PLANNING_MONTH_CONFIG_KEY, value=value))
    else:
        row.value = value
