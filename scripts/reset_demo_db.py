"""Reset backend/db/demo.db to a clean baseline, reloaded from the demo Excel.

Resets via Base.metadata.drop_all(engine) + init_db() rather than deleting the
.db file — sidesteps the Windows sharing-violation this repo has repeatedly
hit (OneDrive sync / a leftover uvicorn --reload worker holding the file
open), matching the same approach scripts/seed_demo.py already used for this
exact problem.

Usage:
    PAYMENTS_DB_PATH=backend/db/demo.db python -m scripts.reset_demo_db
    PAYMENTS_DB_PATH=backend/db/demo.db python -m scripts.reset_demo_db "data/Vendor's Details (Demo 15).xlsx"
"""
import os
import shutil
import sys
from datetime import datetime

os.environ.setdefault("PAYMENTS_DB_PATH", "backend/db/demo.db")

from backend.db.models import Base  # noqa: E402
from backend.db.session import DB_PATH, engine, init_db  # noqa: E402
from backend.ingestion.load_excel import load, print_report  # noqa: E402

DEFAULT_EXCEL = "data/Vendor's Details (Demo 15).xlsx"


def reset(excel_path=DEFAULT_EXCEL):
    if os.path.exists(DB_PATH):
        backup_path = f"{DB_PATH}.backup_{datetime.now():%Y-%m-%d_%H%M%S}"
        shutil.copyfile(DB_PATH, backup_path)  # copy, not move — works even when the file is locked for delete/rename
        print(f"Backed up {DB_PATH} -> {backup_path}")

    Base.metadata.drop_all(engine)
    init_db()
    print_report(load(excel_path))


if __name__ == "__main__":
    reset(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXCEL)
