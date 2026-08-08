"""Engine/session setup for the database (docs/12-database.md).

Postgres in production (Vercel Functions are stateless — SQLite writes don't
persist across invocations), SQLite for local dev/tests. DATABASE_URL set
selects Postgres; unset keeps the original SQLite path unchanged.
"""
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from .models import Base

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    # NullPool: Neon's pooled (-pooler) endpoint already pools via PgBouncer
    # in transaction mode — don't double-pool on top of it. pool_pre_ping
    # guards against a connection going stale between invocations.
    engine = create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)
else:
    DB_PATH = os.environ.get("PAYMENTS_DB_PATH", os.path.join(os.path.dirname(__file__), "app.db"))
    engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)


def _add_missing_columns():
    """Base.metadata.create_all() only creates missing TABLES — it never
    ALTERs an already-existing one. This project has no migration framework
    (single-file SQLite, docs/12-database.md), so a new column added to an
    existing model (e.g. PlanAllocation.override_amount) needs an explicit,
    idempotent ADD COLUMN here or it silently never reaches a DB file that
    predates the model change. Portable SQL (ADD COLUMN), not a
    SQLite-only pragma, so this keeps working under the eventual Postgres
    migration path too.
    """
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue  # brand-new table — create_all() already included every column
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                col_type = column.type.compile(engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"))


def _normalize_category_values():
    """Category-configuration task: `Vendor.category` used to be a real
    SQLAlchemy `Enum(VendorCategory)` column, which — confirmed via
    sqlite3 against the actual DB file — physically stores the enum
    MEMBER NAME ("NORMAL", "MUST_PAY", ...), not its .value ("normal",
    "must_pay", ...). Now that the column is a plain String (models.py),
    every reader expects the lowercase .value form, so old rows are
    normalized once here rather than every reader guessing at both forms.
    All four legacy names are literally their value uppercased, so a bare
    LOWER() is exact — no name/value lookup table needed. Idempotent (a
    no-op once every row is already lowercase); safe to run every startup.
    """
    inspector = inspect(engine)
    if "vendors" not in inspector.get_table_names():
        return
    # Bug fix: this used to open a write transaction (engine.begin() + UPDATE)
    # unconditionally on every init_db() call — i.e. every single load()/
    # upload, forever, not just the one time it's actually needed. SQLite
    # only allows one writer at a time; load() is frequently called with an
    # outer session/transaction already open on this same engine (e.g.
    # commit_upload()), so this second, unrelated write collided with it —
    # "database is locked" on a real upload. A plain SELECT (read-only, no
    # write lock) checks first; the UPDATE only ever runs the one real time
    # any row still needs it, same idempotent-after-first-run shape
    # _add_missing_columns() above already has.
    with engine.connect() as conn:
        needs_fix = conn.execute(text("SELECT 1 FROM vendors WHERE category != LOWER(category) LIMIT 1")).first()
    if needs_fix:
        with engine.begin() as conn:
            conn.execute(text("UPDATE vendors SET category = LOWER(category) WHERE category != LOWER(category)"))


def _backfill_pinned_roles():
    """Pinned-role task: PriorityBucket.pinned_role identifies the Must
    Pay/Commitment guaranteed-funding rows now, not their bucket_key text.
    One-time, idempotent backfill for a DB whose P0/P1 rows predate this
    column — check-then-fix, same pattern as _normalize_category_values()
    above. Only ever matches a row still literally keyed "P0"/"P1" with no
    pinned_role set yet; once set (or once that row's bucket_key is
    renamed away), this is a permanent no-op for it — allocator.py's
    _seed_and_read_buckets() takes over from here for any DB that never
    had a "P0"/"P1" row at all (a fresh table).
    """
    inspector = inspect(engine)
    if "priority_buckets" not in inspector.get_table_names():
        return
    # Read-only check first (same reasoning as _normalize_category_values()
    # above) — this runs on every init_db() call, i.e. every load()/upload,
    # so it must not open a write transaction when there's nothing to fix.
    with engine.connect() as conn:
        needs_fix = conn.execute(
            text("SELECT 1 FROM priority_buckets WHERE bucket_key IN ('P0', 'P1') AND pinned_role IS NULL LIMIT 1")
        ).first()
    if needs_fix:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE priority_buckets SET pinned_role = 'must_pay' WHERE bucket_key = 'P0' AND pinned_role IS NULL")
            )
            conn.execute(
                text("UPDATE priority_buckets SET pinned_role = 'commitment' WHERE bucket_key = 'P1' AND pinned_role IS NULL")
            )


def _fix_vendor_erp_code_uniqueness():
    """Entity-code-collision fix: `Vendor.erp_code` used to be its own
    unique index — wrong, since 28 real vendor pairs in the master sheet
    are different legal entities (ARS/FP) sharing one bare numeric code,
    and that constraint made it impossible to ever store the second one as
    its own row. Real uniqueness key is now (entity, erp_code) (models.py).
    SQLite can't ALTER a UNIQUE constraint in place, so this is an
    explicit, one-time index swap — check-then-fix, same idempotent
    pattern as _normalize_category_values()/_backfill_pinned_roles() above.
    Every vendor already satisfies the old (stricter) single-column
    uniqueness, so creating the new compound index can never fail here —
    this only ever swaps the index, never touches vendor data.
    """
    if engine.dialect.name != "sqlite":
        return  # sqlite_master is SQLite-only; Postgres schema is created fresh with the correct constraint already
    inspector = inspect(engine)
    if "vendors" not in inspector.get_table_names():
        return
    with engine.connect() as conn:
        old_unique_index = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='vendors' "
                "AND sql LIKE 'CREATE UNIQUE INDEX%(erp_code)'"
            )
        ).first()
    if old_unique_index:
        with engine.begin() as conn:
            conn.execute(text(f"DROP INDEX {old_unique_index[0]}"))
            conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_entity_erp_code ON vendors (entity, erp_code)")
            )


def init_db():
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _fix_vendor_erp_code_uniqueness()
    _normalize_category_values()
    _backfill_pinned_roles()


init_db()  # every importer of SessionLocal gets an up-to-date schema, not just load_excel.py's callers
