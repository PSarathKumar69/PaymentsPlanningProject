"""One-off: hard-reset live production data before a fresh Excel upload.

Truncates vendors, monthly_ledger, payments, plan_runs, plan_allocations,
vendor_extra_fields, and audit_log — every vendor and its history, gone.
IDs restart at 1 on next insert.

Deliberately NOT touched: config, priority_buckets, column_mapping_overrides
(app settings — user chose to keep these), and master_workbook (the next
real upload through the app overwrites its "current"/"backup" blobs
anyway, same as any other month).

Irreversible against whatever DATABASE_URL points at. No --yes flag on
purpose — you must type the exact database name at the prompt below, so
this can't be run by accident or scripted around.

Usage:
    DATABASE_URL=postgres://... python -m backend.db.reset_production_data
"""
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url

TABLES = [
    "audit_log",
    "vendor_extra_fields",
    "plan_allocations",
    "payments",
    "plan_runs",
    "monthly_ledger",
    "vendors",
]


def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL must be set to the target Postgres connection string")

    from backend.db.session import engine

    if engine.dialect.name != "postgresql":
        raise SystemExit("DATABASE_URL isn't pointing at Postgres — refusing to run against SQLite")

    url = make_url(db_url)
    print(f"About to PERMANENTLY DELETE every row from: {', '.join(TABLES)}")
    print(f"Target database: {url.host} / {url.database}")
    print("NOT touched: config, priority_buckets, column_mapping_overrides, master_workbook.")
    print()
    confirm = input(f"Type the database name exactly ({url.database}) to confirm, anything else aborts: ")
    if confirm != url.database:
        raise SystemExit("Confirmation did not match — aborted, nothing was deleted.")

    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))

    print("\nDone. All listed tables are now empty, ID sequences reset to 1.")
    print("Settings/config tables untouched. Ready for a fresh upload through the app.")


if __name__ == "__main__":
    main()
