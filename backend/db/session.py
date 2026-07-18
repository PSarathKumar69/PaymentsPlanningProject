"""Engine/session setup for the SQLite database (docs/12-database.md)."""
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from .models import Base

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


def init_db():
    Base.metadata.create_all(engine)
    _add_missing_columns()


init_db()  # every importer of SessionLocal gets an up-to-date schema, not just load_excel.py's callers
