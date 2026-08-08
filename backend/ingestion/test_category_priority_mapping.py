"""Category<->Priority Tag intelligent mapping (this task, see
claude_code_prompt_category_priority_mapping_and_planning_month.md).

Real finding this fixes: the live master sheet's "Category" column holds
"Vendor"/"Non Vendor" (an entity-type flag, not Finance's vocabulary) while
its "Prioity" column holds real P0-P4 tags — category and priority_tag are
two views of the same signal and must be reconciled, not read independently.

Unit tests for the pure derivation functions live here too (no Excel I/O
needed for those), plus end-to-end load() tests against a small synthetic
workbook (single_header_row_sheet.xlsx fixture — same shape as the real
sheet: one header row, Category/Priority Tag already present as dedicated
columns) so each scenario is deterministic and isolated from the real,
externally-mutable master file's own drift (see memory:
demo15-dataset-quirks).
"""
import os
import shutil
import sys

import openpyxl
import pytest

from backend.ingestion.column_mapping import category_from_label, derive_category_and_priority_tag
from backend.shared.enums import VendorCategory

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "test_fixtures")
_FIXTURE = "single_header_row_sheet.xlsx"

# Fixture's two rows: VT0001 = Category "Normal"/Priority Tag "P2" (row 2),
# VT0002 = Category "Must Pay"/Priority Tag "P0" (row 3) — both already
# agree under the confirmed mapping, used as the "no conflict" baseline.
_ROW_VT0001 = 2
_ROW_VT0002 = 3
_CATEGORY_COL = 16
_PRIORITY_TAG_COL = 19


def _build_workbook(tmp_path, cell_overrides, name="sheet.xlsx"):
    """cell_overrides: {(row, col): value} applied on top of the fixture."""
    path = str(tmp_path / name)
    shutil.copy(os.path.join(_FIXTURES_DIR, _FIXTURE), path)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    for (row, col), value in cell_overrides.items():
        ws.cell(row=row, column=col).value = value
    wb.save(path)
    return path


def _fresh_load(excel_path, tmp_path):
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    for mod in ("backend.db.session", "backend.ingestion.load_excel"):
        sys.modules.pop(mod, None)
    from backend.ingestion.load_excel import load

    return load(excel_path=excel_path)


def _vendor(erp_code):
    from backend.db.models import Vendor
    from backend.db.session import SessionLocal

    session = SessionLocal()
    try:
        return session.query(Vendor).filter_by(erp_code=erp_code).one()
    finally:
        session.close()


# ---- category_from_label(): variant acceptance ------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Must Pay", VendorCategory.MUST_PAY),
        ("MUSTPAY", VendorCategory.MUST_PAY),
        ("Must-Pay", VendorCategory.MUST_PAY),
        ("must_pay", VendorCategory.MUST_PAY),
        ("Commitment", VendorCategory.COMMITMENT),
        ("Commit", VendorCategory.COMMITMENT),
        ("COMMITMENT", VendorCategory.COMMITMENT),
        ("Normal", VendorCategory.NORMAL),
        ("normal", VendorCategory.NORMAL),
        ("Inactive", VendorCategory.INACTIVE),
        ("INACTIVE", VendorCategory.INACTIVE),
    ],
)
def test_category_from_label_accepts_variants(raw, expected):
    assert category_from_label(raw) == expected


def test_category_from_label_none_for_unrecognized_and_none():
    assert category_from_label("Vendor") is None
    assert category_from_label("Non Vendor") is None
    assert category_from_label(None) is None


# ---- derive_category_and_priority_tag(): pure logic -------------------------


@pytest.mark.parametrize(
    "tag,expected_category",
    [("P0", VendorCategory.MUST_PAY), ("P1", VendorCategory.COMMITMENT), ("P2", VendorCategory.NORMAL),
     ("P3", VendorCategory.NORMAL), ("P4", VendorCategory.NORMAL), ("P5", VendorCategory.INACTIVE)],
)
def test_derive_numeric_tag_only_derives_category(tag, expected_category):
    category, priority_tag, conflict = derive_category_and_priority_tag(None, tag)
    assert category == expected_category
    assert priority_tag == tag
    assert conflict is None


@pytest.mark.parametrize(
    "category,expected_tag",
    [
        (VendorCategory.MUST_PAY, "P0"),
        (VendorCategory.COMMITMENT, "P1"),
        (VendorCategory.NORMAL, "P2"),  # confirmed default — never P3/P4
        (VendorCategory.INACTIVE, "P5"),
    ],
)
def test_derive_string_label_only_derives_priority_tag(category, expected_tag):
    derived_category, priority_tag, conflict = derive_category_and_priority_tag(category, None)
    assert derived_category == category
    assert priority_tag == expected_tag
    assert conflict is None


def test_derive_both_present_and_agreeing_no_conflict():
    category, priority_tag, conflict = derive_category_and_priority_tag(VendorCategory.COMMITMENT, "P1")
    assert (category, priority_tag, conflict) == (VendorCategory.COMMITMENT, "P1", None)


def test_derive_both_present_and_disagreeing_category_wins():
    """Tag column says P3 (-> Normal) but category column says Commitment —
    category wins, and the conflict names both raw-ish values."""
    category, priority_tag, conflict = derive_category_and_priority_tag(VendorCategory.COMMITMENT, "P3")
    assert category == VendorCategory.COMMITMENT
    assert priority_tag == "P1"
    assert conflict == ("P3", VendorCategory.COMMITMENT)


def test_derive_neither_recognized_returns_none_triple():
    assert derive_category_and_priority_tag(None, None) == (None, None, None)


# ---- end-to-end load(): numeric tag only, category column unrecognized -----


def test_load_numeric_tag_only_derives_category_end_to_end(tmp_path):
    path = _build_workbook(
        tmp_path,
        {
            (_ROW_VT0001, _CATEGORY_COL): "Vendor",  # unrecognized (real-file shape)
            (_ROW_VT0001, _PRIORITY_TAG_COL): "P3",
            (_ROW_VT0002, _CATEGORY_COL): "Vendor",
            (_ROW_VT0002, _PRIORITY_TAG_COL): "P5",
        },
    )
    report = _fresh_load(path, tmp_path)
    assert _vendor("VT0001").category == VendorCategory.NORMAL
    assert _vendor("VT0001").priority_tag == "P3"
    assert _vendor("VT0002").category == VendorCategory.INACTIVE
    assert _vendor("VT0002").priority_tag == "P5"
    assert report["category_priority_conflicts"] == []
    # Whole Category column unrecognized (both rows) -> one aggregated note.
    assert any("don't match any known label for 2 vendors" in n for n in report["data_quality_notes"])


# ---- end-to-end load(): string label only, priority tag column blank ------


def test_load_string_label_only_derives_priority_tag_end_to_end(tmp_path):
    path = _build_workbook(
        tmp_path,
        {
            (_ROW_VT0001, _CATEGORY_COL): "Normal",
            (_ROW_VT0001, _PRIORITY_TAG_COL): None,
            (_ROW_VT0002, _CATEGORY_COL): "Inactive",
            (_ROW_VT0002, _PRIORITY_TAG_COL): None,
        },
    )
    _fresh_load(path, tmp_path)
    assert _vendor("VT0001").category == VendorCategory.NORMAL
    assert _vendor("VT0001").priority_tag == "P2"  # confirmed default, never P3/P4
    assert _vendor("VT0002").category == VendorCategory.INACTIVE
    assert _vendor("VT0002").priority_tag == "P5"


# ---- end-to-end load(): both present and agreeing -> no conflict ----------


def test_load_both_agreeing_no_conflict_logged(tmp_path):
    # Fixture as-is: VT0001 Normal/P2, VT0002 Must Pay/P0 — both agree.
    path = _build_workbook(tmp_path, {})
    report = _fresh_load(path, tmp_path)
    assert _vendor("VT0001").category == VendorCategory.NORMAL
    assert _vendor("VT0001").priority_tag == "P2"
    assert _vendor("VT0002").category == VendorCategory.MUST_PAY
    assert _vendor("VT0002").priority_tag == "P0"
    assert report["category_priority_conflicts"] == []


# ---- end-to-end load(): both present and disagreeing -> category wins -----


def test_load_conflict_category_wins_and_is_logged_and_audited(tmp_path):
    path = _build_workbook(
        tmp_path,
        {
            (_ROW_VT0001, _CATEGORY_COL): "Commitment",
            (_ROW_VT0001, _PRIORITY_TAG_COL): "P3",  # P3 -> Normal, disagrees with Commitment
        },
    )
    report = _fresh_load(path, tmp_path)
    vendor = _vendor("VT0001")
    assert vendor.category == VendorCategory.COMMITMENT
    assert vendor.priority_tag == "P1"

    assert len(report["category_priority_conflicts"]) == 1
    note = report["category_priority_conflicts"][0]
    assert "VT0001" in note and "P3" in note and "Commitment" in note

    from backend.db.models import AuditLog
    from backend.db.session import SessionLocal

    session = SessionLocal()
    try:
        entry = session.query(AuditLog).filter_by(vendor_id=vendor.id, field_name="category_priority_conflict").one()
        assert "P3" in entry.old_value
        assert "Commitment" in entry.new_value
    finally:
        session.close()


# ---- end-to-end load(): neither recognized -> falls back, logged once -----


def test_load_neither_recognized_falls_back_to_normal_and_logs_once(tmp_path):
    path = _build_workbook(
        tmp_path,
        {
            (_ROW_VT0001, _CATEGORY_COL): "Vendor",
            (_ROW_VT0001, _PRIORITY_TAG_COL): "not-a-tag",
            (_ROW_VT0002, _CATEGORY_COL): "Non Vendor",
            (_ROW_VT0002, _PRIORITY_TAG_COL): "also-not-a-tag",
        },
    )
    report = _fresh_load(path, tmp_path)
    v1, v2 = _vendor("VT0001"), _vendor("VT0002")
    assert v1.category == VendorCategory.NORMAL  # brand-new vendor, model default
    assert v1.priority_tag is None
    assert v2.category == VendorCategory.NORMAL
    assert v2.priority_tag is None

    category_notes = [n for n in report["data_quality_notes"] if "Category column" in n]
    assert len(category_notes) == 1  # aggregated, not one per vendor
    assert "2 vendors" in category_notes[0]


# ---- Category-configuration task: a custom category, seeded via the ------
# ---- Configuration API BEFORE ingestion, must round-trip through -------
# ---- category_from_label()/derive_category_and_priority_tag() too ------


def _fresh_load_with_custom_category(excel_path, tmp_path, bucket_key, category_name):
    """Same isolated-fresh-DB pattern as _fresh_load(), except the custom
    bucket/category is seeded into THIS fresh DB before load() runs — a
    real category Finance added through Configuration must be recognized
    by ingestion on the very next upload, not just by the API/allocator."""
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    for mod in (
        "backend.db.session",
        "backend.ingestion.load_excel",
        "backend.configuration.priority_bucket_edits",
        "backend.models.new_model_2.allocator",
    ):
        sys.modules.pop(mod, None)
    from backend.configuration.priority_bucket_edits import add_bucket, list_buckets
    from backend.db.session import SessionLocal
    from backend.ingestion.load_excel import load

    session = SessionLocal()
    try:
        list_buckets(session=session)  # seed today's P2-P5 defaults first, same as a real DB would have
        add_bucket(bucket_key, bucket_key, 0.80, 0.20, 99, category_name, session=session)
        session.commit()
    finally:
        session.close()

    return load(excel_path=excel_path)


def test_category_from_label_recognizes_a_live_custom_category(tmp_path):
    os.environ["PAYMENTS_DB_PATH"] = str(tmp_path / "test.db")
    for mod in ("backend.db.session", "backend.configuration.priority_bucket_edits", "backend.models.new_model_2.allocator"):
        sys.modules.pop(mod, None)
    from backend.configuration.priority_bucket_edits import add_bucket, list_buckets
    from backend.db.session import SessionLocal
    from backend.ingestion.column_mapping import category_from_label, derive_category_and_priority_tag

    session = SessionLocal()
    try:
        list_buckets(session=session)
        add_bucket("P6", "P6", 0.80, 0.20, 99, "Contractor", session=session)
        session.commit()

        assert category_from_label("Contractor", session=session) == "Contractor"
        assert category_from_label("CONTRACTOR", session=session) == "Contractor"  # case-insensitive, same as fixed labels
        assert category_from_label("Contractor") is None  # no session -> old fixed 4-value fallback only

        category, priority_tag, conflict = derive_category_and_priority_tag(None, "P6", session=session)
        assert (category, priority_tag, conflict) == ("Contractor", "P6", None)

        category, priority_tag, conflict = derive_category_and_priority_tag("Contractor", None, session=session)
        assert (category, priority_tag, conflict) == ("Contractor", "P6", None)
    finally:
        session.rollback()
        session.close()


def test_load_recognizes_a_custom_category_end_to_end(tmp_path):
    path = _build_workbook(
        tmp_path,
        {
            (_ROW_VT0001, _CATEGORY_COL): "Contractor",
            (_ROW_VT0001, _PRIORITY_TAG_COL): None,
        },
    )
    _fresh_load_with_custom_category(path, tmp_path, "P6", "Contractor")
    vendor = _vendor("VT0001")
    assert vendor.category == "Contractor"
    assert vendor.priority_tag == "P6"
