"""Tests against the real ingested database (backend/db/app.db) and a
TEMPORARY COPY of the real Excel file — the real Excel is never touched.

update_vendor_field() only commits/publishes when it OWNS its own session
(session=None). Tests proving DB+Excel actually landed must therefore call
it with session=None (a real commit against app.db) and explicitly revert
afterward via `_revert()` — there is no rollback available for a call that
already committed for real. Tests proving only the failure path, or only
that update_vendor_field's own DB-side flush is visible within a shared
session, can still pass a caller-supplied session (nothing gets committed
there either way).
"""
import os
import shutil
from datetime import date

import openpyxl
import pytest
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session as SASession

from backend.configuration.vendor_edits import FIELD_TO_EXCEL_HEADER, update_vendor_field
from backend.db.models import AuditLog, MonthlyLedger, Vendor
from backend.db.session import SessionLocal
from backend.ingestion.load_excel import EXCEL_PATH
from backend.shared.enums import ChangeSource, VendorCategory


def _copy_excel(tmp_path):
    dest = tmp_path / "copy.xlsx"
    shutil.copy(EXCEL_PATH, dest)
    return str(dest)


def _read_cell(excel_path, header_text, erp_code):
    wb = openpyxl.load_workbook(excel_path)
    ws = wb.active
    header_row = 3
    col = next(
        c
        for c in range(1, ws.max_column + 1)
        if str(ws.cell(row=header_row, column=c).value or "").strip().lower() == header_text.lower()
    )
    for row in range(4, ws.max_row + 1):
        if ws.cell(row=row, column=2).value == erp_code:  # ERP Code is column B
            return ws.cell(row=row, column=col).value
    raise AssertionError(f"{erp_code} not found in {excel_path}")


def _vendor_id(erp_code):
    session = SessionLocal()
    try:
        return session.query(Vendor).filter_by(erp_code=erp_code).one().id
    finally:
        session.close()


def _revert(vendor_id, field, old_value):
    """Undo a real (owns_session=True) commit made during a test: reset the
    field and delete the audit_log row(s) it created. No Excel-side cleanup
    is needed since every test publishes to a tmp_path copy that pytest
    discards on its own."""
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(id=vendor_id).one()
        setattr(vendor, field, old_value)
        session.query(AuditLog).filter_by(vendor_id=vendor_id, field_name=field).delete()
        session.commit()
    finally:
        session.close()


def test_category_updates_db_excel_and_audit_log(tmp_path):
    excel_copy = _copy_excel(tmp_path)
    vendor_id = _vendor_id("V00400")

    try:
        old, new = update_vendor_field(vendor_id, "category", VendorCategory.MUST_PAY, excel_path=excel_copy)

        assert old == VendorCategory.NORMAL
        assert new == VendorCategory.MUST_PAY
        assert _read_cell(excel_copy, "Category", "V00400") == "Must Pay"  # published for real

        verify = SessionLocal()
        try:
            assert verify.query(Vendor).filter_by(id=vendor_id).one().category == VendorCategory.MUST_PAY
            entry = verify.query(AuditLog).filter_by(vendor_id=vendor_id, field_name="category").one()
            assert entry.old_value == "Normal"  # clean Finance-facing text, not a Python repr
            assert entry.new_value == "Must Pay"
            assert entry.source == ChangeSource.UI_EDIT.value
        finally:
            verify.close()
    finally:
        _revert(vendor_id, "category", VendorCategory.NORMAL)


def test_commitment_months_updates_db_and_excel(tmp_path):
    excel_copy = _copy_excel(tmp_path)
    vendor_id = _vendor_id("V01542")

    try:
        old, new = update_vendor_field(vendor_id, "commitment_months", 6, excel_path=excel_copy)

        assert old == 1
        assert new == 6
        assert _read_cell(excel_copy, "Commitment Months", "V01542") == 6

        verify = SessionLocal()
        try:
            assert verify.query(Vendor).filter_by(id=vendor_id).one().commitment_months == 6
        finally:
            verify.close()
    finally:
        _revert(vendor_id, "commitment_months", 1)


def test_assigned_week_updates_db_and_excel_and_can_be_cleared(tmp_path):
    excel_copy = _copy_excel(tmp_path)
    vendor_id = _vendor_id("V00400")

    original = None
    try:
        old, new = update_vendor_field(vendor_id, "assigned_week", 3, excel_path=excel_copy)
        original = old
        assert old == 2
        assert new == 3
        assert _read_cell(excel_copy, "Assigned Week", "V00400") == 3

        # Clearing back to None is valid too.
        old2, new2 = update_vendor_field(vendor_id, "assigned_week", None, excel_path=excel_copy)
        assert old2 == 3
        assert new2 is None
        assert _read_cell(excel_copy, "Assigned Week", "V00400") is None

        verify = SessionLocal()
        try:
            assert verify.query(Vendor).filter_by(id=vendor_id).one().assigned_week is None
        finally:
            verify.close()
    finally:
        if original is not None:
            _revert(vendor_id, "assigned_week", original)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("category", "not_a_real_category"),
        ("commitment_months", 0),
        ("commitment_months", -3),
        ("commitment_months", 2.5),
        ("assigned_week", 0),
        ("assigned_week", 6),
        ("assigned_week", "W2"),
        ("made_up_field", "anything"),
    ],
)
def test_invalid_values_rejected_before_touching_db_or_excel(tmp_path, field, bad_value):
    # Validation raises before session/Excel are ever touched, regardless of
    # session ownership — a caller-supplied session is fine here.
    excel_copy = _copy_excel(tmp_path)
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
        with pytest.raises(ValueError):
            update_vendor_field(vendor.id, field, bad_value, session=session, excel_path=excel_copy)

        assert session.query(AuditLog).filter_by(vendor_id=vendor.id).count() == 0
        wb = openpyxl.load_workbook(excel_copy)
        header_texts = {str(c.value).strip().lower() for c in wb.active[3] if c.value}
        for header in FIELD_TO_EXCEL_HEADER.values():
            assert header.lower() not in header_texts  # no column was ever created
    finally:
        session.rollback()
        session.close()


def test_assigned_week_validated_against_calendar_week_count(tmp_path):
    """CLAUDE.md rule 7's confirmed fix: assigned_week's valid range comes
    from the calendar (this cycle's actual week count), not a hardcoded 5.

    Real, unmodified state: the real ledger's latest month is May-2026 (31
    days -> 5 weeks), so W5 is accepted with no construction needed.
    Constructed: adding a later MonthlyLedger row for Feb-2099 (28 days ->
    4 weeks) makes that the new latest month across the whole DB — W5 must
    now be rejected while W4 is still accepted.
    """
    excel_copy = _copy_excel(tmp_path)
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()

        _, new = update_vendor_field(vendor.id, "assigned_week", 5, session=session, excel_path=excel_copy)
        assert new == 5  # 5-week month (real May-2026) accepts W5

        session.add(
            MonthlyLedger(
                vendor_id=vendor.id,
                month=date(2099, 2, 1),
                payable=0,
                payment=0,
                opening_balance=0,
                closing_balance=0,
            )
        )
        session.flush()

        with pytest.raises(ValueError):
            update_vendor_field(vendor.id, "assigned_week", 5, session=session, excel_path=excel_copy)

        _, new2 = update_vendor_field(vendor.id, "assigned_week", 4, session=session, excel_path=excel_copy)
        assert new2 == 4  # 4-week month (constructed Feb-2099) still accepts W4
    finally:
        session.rollback()
        session.close()


def test_owns_session_commits_then_publishes_in_order(tmp_path, monkeypatch):
    """Proof point (1): session=None commits, then publishes — in that order."""
    excel_copy = _copy_excel(tmp_path)
    vendor_id = _vendor_id("V00400")

    call_order = []
    real_commit = SASession.commit

    def _tracked_commit(self):
        call_order.append("commit")
        return real_commit(self)

    real_replace = os.replace

    def _tracked_replace(src, dst):
        call_order.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(SASession, "commit", _tracked_commit)
    monkeypatch.setattr(os, "replace", _tracked_replace)

    try:
        update_vendor_field(vendor_id, "commitment_months", 4, excel_path=excel_copy)
        assert call_order == ["commit", "replace"]
    finally:
        _revert(vendor_id, "commitment_months", 1)


def test_owns_session_excel_stage_failure_rolls_back_and_never_publishes(tmp_path, monkeypatch):
    """Proof point (2), failure before commit is even attempted: staging the
    Excel edit fails -> rollback, no column ever created, no publish."""
    excel_copy = _copy_excel(tmp_path)

    def _raise_on_save(self, filename):
        raise OSError("simulated write-back failure")

    monkeypatch.setattr(openpyxl.Workbook, "save", _raise_on_save)

    vendor_id = _vendor_id("V00400")
    lookup = SessionLocal()
    original_category = lookup.query(Vendor).filter_by(id=vendor_id).one().category
    lookup.close()

    with pytest.raises(OSError):
        update_vendor_field(vendor_id, "category", VendorCategory.MUST_PAY, excel_path=excel_copy)

    verify = SessionLocal()
    try:
        assert verify.query(Vendor).filter_by(id=vendor_id).one().category == original_category  # never committed
        assert verify.query(AuditLog).filter_by(vendor_id=vendor_id).count() == 0
    finally:
        verify.close()

    wb = openpyxl.load_workbook(excel_copy)
    header_texts = {str(c.value).strip().lower() for c in wb.active[3] if c.value}
    assert "category" not in header_texts  # never published


def test_owns_session_db_commit_failure_after_excel_staged_rolls_back_and_never_publishes(tmp_path, monkeypatch):
    """Proof point (2), failure after staging succeeds: the DB commit itself
    fails -> rollback, no publish. The mocked commit() never actually
    touches the real app.db, so this is safe to run against it."""
    excel_copy = _copy_excel(tmp_path)
    vendor_id = _vendor_id("V00400")

    def _raise_on_commit(self):
        raise RuntimeError("simulated DB commit failure")

    monkeypatch.setattr(SASession, "commit", _raise_on_commit)

    with pytest.raises(RuntimeError):
        update_vendor_field(vendor_id, "commitment_months", 9, excel_path=excel_copy)

    verify = SessionLocal()
    try:
        assert verify.query(Vendor).filter_by(id=vendor_id).one().commitment_months == 1  # real app.db untouched
    finally:
        verify.close()

    wb = openpyxl.load_workbook(excel_copy)
    header_texts = {str(c.value).strip().lower() for c in wb.active[3] if c.value}
    assert "commitment months" not in header_texts  # staged edit was never published


def test_caller_supplied_session_failure_does_not_rollback_others_pending_changes(tmp_path):
    """Proof point (3): a failure inside update_vendor_field must not touch
    a caller-supplied session's OTHER pending, uncommitted work."""
    excel_copy = _copy_excel(tmp_path)
    session = SessionLocal()
    try:
        other_vendor = session.query(Vendor).filter_by(erp_code="V01542").one()
        other_vendor.commitment_months = 9  # unrelated pending change, not yet committed

        with pytest.raises(NoResultFound):
            update_vendor_field(-1, "category", VendorCategory.MUST_PAY, session=session, excel_path=excel_copy)

        assert other_vendor.commitment_months == 9  # survived — not rolled back
    finally:
        session.rollback()
        session.close()


def test_caller_supplied_session_never_publishes(tmp_path, monkeypatch):
    """Proof point (4): a caller-supplied session never calls os.replace(),
    under any circumstance — even on a successful edit."""
    excel_copy = _copy_excel(tmp_path)
    calls = []
    monkeypatch.setattr(os, "replace", lambda *args, **kwargs: calls.append((args, kwargs)))

    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
        old, new = update_vendor_field(vendor.id, "category", VendorCategory.MUST_PAY, session=session, excel_path=excel_copy)

        assert calls == []  # os.replace never called
        assert new == VendorCategory.MUST_PAY
        assert vendor.category == VendorCategory.MUST_PAY  # DB-side flush is visible in the shared session
        # Nothing was published — the real column must not exist in the Excel copy either.
        wb = openpyxl.load_workbook(excel_copy)
        header_texts = {str(c.value).strip().lower() for c in wb.active[3] if c.value}
        assert "category" not in header_texts
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize(
    "field,value1,value2",
    [
        ("category", VendorCategory.MUST_PAY, VendorCategory.COMMITMENT),
        ("commitment_months", 3, 6),
        ("assigned_week", 1, 4),
    ],
)
def test_column_reused_not_duplicated_across_vendors(tmp_path, field, value1, value2):
    """One shared code path (_find_or_create_column) covers all three
    fields — a second vendor's edit must reuse the column the first
    vendor's edit created, never duplicate it. Uses session=None for both
    edits since only an owning call ever touches Excel."""
    excel_copy = _copy_excel(tmp_path)
    v1_id = _vendor_id("V00400")
    v2_id = _vendor_id("V01542")

    reverts = []
    try:
        old1, _ = update_vendor_field(v1_id, field, value1, excel_path=excel_copy)
        reverts.append((v1_id, old1))
        old2, _ = update_vendor_field(v2_id, field, value2, excel_path=excel_copy)
        reverts.append((v2_id, old2))

        wb = openpyxl.load_workbook(excel_copy)
        header_texts = [str(c.value).strip().lower() for c in wb.active[3] if c.value]
        assert header_texts.count(FIELD_TO_EXCEL_HEADER[field].lower()) == 1
    finally:
        for vid, old in reverts:
            _revert(vid, field, old)


def test_assigned_week_edit_reflected_in_build_weekly_view(tmp_path):
    """Confirms assigned_week's stored type (plain int 1-5 or None) matches
    exactly what planner.py already expects when grouping by week. Uses a
    caller-supplied session throughout — this only relies on the DB-side
    flush being visible within that same session, which update_vendor_field
    still does regardless of session ownership; nothing here checks Excel."""
    from backend.models.model2_min_funds_advisory.advisor import generate_plan
    from backend.weekly_planning.planner import build_weekly_view

    excel_copy = _copy_excel(tmp_path)
    session = SessionLocal()
    try:
        vendor = session.query(Vendor).filter_by(erp_code="V00400").one()
        assert vendor.assigned_week == 2  # real, confirmed baseline

        update_vendor_field(vendor.id, "assigned_week", 4, session=session, excel_path=excel_copy)
        assert vendor.assigned_week == 4

        plan = generate_plan(available_funds=100_000_000, session=session)
        view = build_weekly_view(plan["allocations"], session=session, persist=False)
        row = view["detail"][view["detail"]["vendor_id"] == vendor.id].iloc[0]
        assert row["assigned_week"] == 4  # picked up the edit correctly, grouped under the new week
    finally:
        session.rollback()
        session.close()
