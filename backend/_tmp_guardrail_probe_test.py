import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def test_probe_commit_against_real_db_is_blocked():
    real_path = os.path.join(os.path.dirname(__file__), "db", "app.db")
    engine = create_engine(f"sqlite:///{real_path}")
    Session = sessionmaker(bind=engine)
    s = Session()
    with pytest.raises(RuntimeError, match="TEST ISOLATION VIOLATION"):
        s.commit()
    s.close()

def test_probe_excel_overwrite_is_blocked():
    real_excel = os.path.join(os.path.dirname(__file__), "..", "data", "Vendor's Details.xlsx")
    dummy_src = os.path.join(os.path.dirname(__file__), "..", "data", "_dummy_probe.xlsx")
    with open(dummy_src, "w") as f:
        f.write("x")
    try:
        with pytest.raises(RuntimeError, match="TEST ISOLATION VIOLATION"):
            os.replace(dummy_src, real_excel)
    finally:
        if os.path.exists(dummy_src):
            os.remove(dummy_src)
