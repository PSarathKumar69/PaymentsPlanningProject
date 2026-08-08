"""One-off follow-up to migrate_sqlite_to_postgres.py: the main run
successfully copied every table's data (verified via manual row-count
check), but crashed on a dropped connection during its own final commit
before it ever reached the sequence-reset block below that call. Postgres's
auto-increment sequences for each table are still at their default (1),
not bumped past the migrated rows' real IDs — the next real INSERT
(a new payment, a new plan_run, a new vendor) would collide with an
already-used ID. This script only touches sequences, never table data,
and is safe to run against a database that already has real rows in it
(unlike the main migration script, which would try to re-insert and fail
on a UniqueViolation if run twice against the same target).

Usage:
    DATABASE_URL=postgres://... python -m backend.db.reset_sequences
"""
import os

from sqlalchemy import func, inspect, select

from backend.db.models import Base


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL must be set to the target Postgres connection string")

    from backend.db.session import engine

    if engine.dialect.name != "postgresql":
        raise SystemExit("DATABASE_URL isn't pointing at Postgres — nothing to reset")

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if "id" not in table.c or table.name not in inspector.get_table_names():
                continue
            max_id = conn.execute(select(func.max(table.c.id))).scalar()
            if max_id is None:
                print(f"{table.name}: no rows, nothing to reset")
                continue
            conn.execute(select(func.setval(func.pg_get_serial_sequence(table.name, "id"), max_id)))
            print(f"{table.name}: sequence reset to {max_id}")


if __name__ == "__main__":
    main()
