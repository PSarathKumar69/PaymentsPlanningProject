"""Test-suite safety net (audit task, 2026-07-31): a missing PAYMENTS_DB_PATH
override or excel_path=/backup_path= override has now let a test corrupt the
real production database/Excel three separate times (see CLAUDE.md's project
history and memory/bug_duplicate_erp_code_vendor_loss.md-adjacent incidents).
Patching one offending test each time doesn't stop the next one from making
the same mistake, so this makes the mistake impossible to miss instead: for
every test under backend/, any REAL commit against the real backend/db/app.db
or any REAL overwrite of the real data/Vendor's Details.xlsx (or its backup)
raises immediately, naming the exact call that did it.

Reading the real app.db is still allowed on purpose — several test files
(test_min_funds.py, test_min_funds_v2.py, test_payment_logging.py,
test_plan_history.py, test_planner.py, test_priority_bucket_edits.py,
test_allocator.py) deliberately query it directly and always
session.rollback(), never session.commit(), by design (see their own module
docstrings). This guard only trips on an actual commit/actual file
overwrite, not on merely opening a session against the real path — so that
already-safe pattern keeps working, while a test that slips and calls
session.commit() (or writes the real Excel) on that same session, or any
other, is caught immediately.
"""
import os

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as SASession

_REAL_DB_PATH = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(__file__), "db", "app.db")))
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_REAL_EXCEL_PATH = os.path.normcase(os.path.abspath(os.path.join(_DATA_DIR, "Vendor's Details.xlsx")))
_REAL_EXCEL_BACKUP_PATH = os.path.normcase(os.path.abspath(os.path.join(_DATA_DIR, "Vendor's Details.backup.xlsx")))

# Postgres migration: once bound via DATABASE_URL, url.database is a db name
# ("neondb"), not a path — the SQLite path comparison below silently stops
# catching a leaked write. Snapshot the real (host, database) once at import
# time so a test that leaks onto production Postgres is still caught.
_REAL_DATABASE_URL = os.environ.get("DATABASE_URL")
_REAL_PG_HOST_DB = None
if _REAL_DATABASE_URL:
    _real_url = make_url(_REAL_DATABASE_URL)
    _REAL_PG_HOST_DB = (_real_url.host, _real_url.database)


def _bound_db_path(session):
    try:
        db_path = session.get_bind().url.database
    except Exception:
        return None
    return os.path.normcase(os.path.abspath(db_path)) if db_path else None


def _bound_pg_host_db(session):
    try:
        url = session.get_bind().url
    except Exception:
        return None
    if url.get_backend_name() == "sqlite":
        return None
    return (url.host, url.database)


@pytest.fixture(autouse=True)
def _forbid_real_writes(monkeypatch):
    real_commit = SASession.commit

    def _guarded_commit(self, *args, **kwargs):
        bound = _bound_db_path(self)
        if bound == _REAL_DB_PATH:
            raise RuntimeError(
                f"TEST ISOLATION VIOLATION: session.commit() was just called against the REAL "
                f"production database ({_REAL_DB_PATH}). Point PAYMENTS_DB_PATH at a tmp_path file "
                "and pop backend.db.session (and every backend.* module that imports SessionLocal "
                "at its own module level) from sys.modules BEFORE importing anything that opens a "
                "session — see any _fresh_db_and_master()/_fresh_app() helper already in this "
                "codebase for the pattern. Reading the real DB (always via session.rollback(), "
                "never session.commit()) is still fine — only a real commit trips this."
            )
        if _REAL_PG_HOST_DB is not None and _bound_pg_host_db(self) == _REAL_PG_HOST_DB:
            raise RuntimeError(
                "TEST ISOLATION VIOLATION: session.commit() was just called against the REAL "
                f"production Postgres database ({_REAL_PG_HOST_DB[0]}/{_REAL_PG_HOST_DB[1]}, from "
                "DATABASE_URL). Unset DATABASE_URL for tests (falls back to a tmp_path SQLite file) "
                "or point it at a throwaway database — never the real one."
            )
        return real_commit(self, *args, **kwargs)

    monkeypatch.setattr(SASession, "commit", _guarded_commit)

    real_replace = os.replace

    def _guarded_replace(src, dst):
        dst_norm = os.path.normcase(os.path.abspath(dst))
        if dst_norm in (_REAL_EXCEL_PATH, _REAL_EXCEL_BACKUP_PATH):
            raise RuntimeError(
                f"TEST ISOLATION VIOLATION: os.replace() was just called to overwrite the REAL "
                f"master Excel ({dst_norm}). Pass an explicit excel_path=/backup_path= tmp_path "
                "override to commit_upload()/update_vendor_field()/load()/revert_upload() — never "
                "let one fall through to the real EXCEL_PATH/MASTER_EXCEL_BACKUP_PATH default."
            )
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _guarded_replace)
