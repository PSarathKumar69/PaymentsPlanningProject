"""One-off migration: copy backend/db/app.db (SQLite) into the Postgres
database named by DATABASE_URL. Run by hand once before the Vercel deploy —
not wired into app startup.

Usage:
    DATABASE_URL=postgres://...neon-direct-connection... \\
        python -m backend.db.migrate_sqlite_to_postgres [path/to/app.db]

Reads every table via SQLAlchemy Core against a plain SQLite engine (so JSON/
Boolean/Enum/Date columns come back as real Python objects, not raw SQLite
text/int — no manual per-dialect type coercion needed), writes them into the
Postgres engine backend.db.session already builds from DATABASE_URL, in
Base.metadata.sorted_tables order (respects FK dependencies), then resets
each table's integer PK sequence so post-migration inserts don't collide
with copied IDs.
"""
import os
import sys

from sqlalchemy import create_engine, func, select

from backend.db.models import Base


def migrate(sqlite_path: str) -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL must be set to the target Postgres connection string")
    if not os.path.isfile(sqlite_path):
        raise SystemExit(f"SQLite source not found: {sqlite_path}")

    from backend.db.session import engine as target_engine
    from backend.db.session import init_db

    init_db()  # create schema on the Postgres target before copying data

    source_engine = create_engine(f"sqlite:///{sqlite_path}")

    with source_engine.connect() as src, target_engine.begin() as dst:
        for table in Base.metadata.sorted_tables:
            rows = [dict(row) for row in src.execute(select(table)).mappings()]
            if rows:
                dst.execute(table.insert(), rows)
            print(f"{table.name}: {len(rows)} rows copied")

    if target_engine.dialect.name == "postgresql":
        with target_engine.begin() as dst:
            for table in Base.metadata.sorted_tables:
                if "id" not in table.c:
                    continue
                max_id = dst.execute(select(func.max(table.c.id))).scalar()
                if max_id is None:
                    continue
                dst.execute(
                    select(func.setval(func.pg_get_serial_sequence(table.name, "id"), max_id))
                )
                print(f"{table.name}: sequence reset to {max_id}")


if __name__ == "__main__":
    default_path = os.path.join(os.path.dirname(__file__), "app.db")
    migrate(sys.argv[1] if len(sys.argv) > 1 else default_path)
