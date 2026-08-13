"""Persistence for Finance's confirmed current-cycle planning month (Main-tab
upload-confirm task) — same Config-backed get/set pattern as
backend/ingestion/column_mapping_store.py's get_sheet_start_month()/
set_sheet_start_month(). Exists so a planning month is readable as soon as
Finance confirms one in the upload modal, before any New Model 2 plan_run
has ever been created this cycle — resolve_planning_month() below falls
back to this only after checking the latest plan_run.

parse_planning_month()/month_minus_one()/resolve_planning_month() (payment-
tracking as_of fix, this task) used to be private (underscore-prefixed)
functions defined only in backend/api/routers/new_model_2.py. Moved here,
one source of truth (CLAUDE.md rule 7), once GET /vendors/payment-tracking
needed the exact same "planning month -> its own as_of anchor" resolution
new_model_2.py's finalized-plan export already used — a second,
independently-typed copy in vendors.py would have been exactly the kind of
drift risk that caused the V00036 207.66% bug in the first place.
new_model_2.py now imports these under its original underscore names
(`month_minus_one as _month_minus_one`, etc.) so none of its many existing
call sites needed to change.
"""
from datetime import date

from backend.db.models import Config, PlanRun
from backend.shared.constants import NEW_MODEL_2_PLAN_RUN_LABEL

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


def parse_planning_month(value: str) -> date:
    """"YYYY-MM" (docs/14: month + year, no day) -> the first-of-month date
    PlanRun.month/aging as_of both use. Raises ValueError on a bad format —
    callers turn that into an HTTP 400."""
    try:
        year_str, month_str = value.split("-")
        return date(int(year_str), int(month_str), 1)
    except (ValueError, AttributeError):
        raise ValueError(f"planning_month must be 'YYYY-MM', got {value!r}") from None


def month_minus_one(d: date) -> date:
    """The calendar month immediately before `d` (both as first-of-month
    dates) — docs/14: "the aging reference point is planning month minus
    one calendar month"."""
    if d.month == 1:
        return date(d.year - 1, 12, 1)
    return date(d.year, d.month - 1, 1)


def resolve_planning_month(session, requested: date | None) -> date:
    """docs/14: required on the first Generate Plan of a cycle, optional
    (reused) on every regeneration after that. Fallback order (Main-tab
    upload-confirm task, section 3): (1) explicitly requested, (2) latest
    New Model 2 plan_run this cycle, (3) the persisted
    planning.current_planning_month config value (set the moment Finance
    confirms it in the upload modal, before any plan_run exists), (4) raise
    — callers turn that into an HTTP 400 (generate-plan) or an empty/no-op
    response (plan-runs history, finalize — nothing to show/finalize yet
    either).

    Reset-cycle fix: (2) and (3) are compared by calendar month, not treated
    as a strict priority order — whichever is the LATER month wins, rather
    than always preferring "latest plan_run" outright. Before Reset existed,
    the latest plan_run's month was always >= the stored config's month (new
    cycles only ever move forward), so a strict priority order and a
    "later of the two" comparison gave the same answer and this distinction
    never mattered. Reset (backend/api/routers/new_model_2.py's
    reset-cycle endpoint) is the first code path that can leave an OLDER
    plan_run as the only one remaining while the stored config still points
    at the newer, now-plan-less current cycle — a strict "plan_run always
    wins" rule would then resolve every endpoint back to the stale old
    cycle instead of the fresh post-reset state. Comparing by month keeps
    the existing, tested "generate for a later month supersedes the stored
    config" behavior (test_api.py::
    test_current_planning_month_endpoint_reflects_persisted_config_then_latest_plan_run)
    while also fixing the reset case."""
    if requested is not None:
        return requested
    latest = (
        session.query(PlanRun)
        .filter(PlanRun.model_used == NEW_MODEL_2_PLAN_RUN_LABEL)
        .order_by(PlanRun.created_at.desc(), PlanRun.id.desc())
        .first()
    )
    stored = get_current_planning_month(session)
    stored_date = parse_planning_month(stored) if stored is not None else None
    if latest is not None and stored_date is not None:
        return max(latest.month, stored_date)
    if latest is not None:
        return latest.month
    if stored_date is not None:
        return stored_date
    raise ValueError("planning_month is required for the first Generate Plan of a cycle")
