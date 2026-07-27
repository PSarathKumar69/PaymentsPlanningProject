"""Tests against the real ingested database (backend/db/app.db) — no synthetic
data, always session.rollback()'d, never committed. Same convention as
backend/configuration/test_system_config_edits.py."""
import pytest

from backend.configuration.priority_bucket_edits import add_bucket, list_buckets, remove_bucket, update_bucket
from backend.db.models import AuditLog, PriorityBucket, Vendor
from backend.db.session import SessionLocal
from backend.shared.enums import ChangeSource, VendorCategory, VendorPriorityTag


def test_list_buckets_seeds_todays_p2_through_p5_on_first_call():
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        keys = {b["bucket_key"] for b in buckets}
        assert {"P2", "P3", "P4", "P5"} <= keys
        by_key = {b["bucket_key"]: b for b in buckets}
        assert by_key["P2"]["ceiling_pct"] == pytest.approx(0.95)
        assert by_key["P5"]["ceiling_pct"] == pytest.approx(0.50)
        assert by_key["P5"]["floor_pct"] == pytest.approx(0.10)
        # ordered by rotation_position — P2 highest priority, P5 lowest
        assert [b["bucket_key"] for b in buckets] == sorted(keys, key=lambda k: by_key[k]["rotation_position"])
    finally:
        session.rollback()
        session.close()


def test_add_bucket_writes_row_and_audit_log():
    session = SessionLocal()
    try:
        list_buckets(session=session)  # ensure P2-P5 seeded first, same as a real call would see
        add_bucket("P6", "P6", 0.70, 0.15, 4, source=ChangeSource.UI_EDIT, session=session)

        row = session.query(PriorityBucket).filter_by(bucket_key="P6").one()
        assert row.display_label == "P6"
        assert float(row.ceiling_pct) == pytest.approx(0.70)
        assert float(row.floor_pct) == pytest.approx(0.15)
        assert row.rotation_position == 4

        entry = session.query(AuditLog).filter_by(field_name="priority_bucket.P6").one()
        assert entry.old_value is None
        assert "P6" in entry.new_value
        assert entry.source == ChangeSource.UI_EDIT.value
    finally:
        session.rollback()
        session.close()


def test_add_bucket_duplicate_key_rejected():
    session = SessionLocal()
    try:
        list_buckets(session=session)
        with pytest.raises(ValueError):
            add_bucket("P2", "Duplicate P2", 0.50, 0.10, 50, session=session)
    finally:
        session.rollback()
        session.close()


def test_add_bucket_duplicate_rotation_position_rejected():
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        taken_position = buckets[0]["rotation_position"]
        with pytest.raises(ValueError):
            add_bucket("P7", "P7", 0.50, 0.10, taken_position, session=session)
    finally:
        session.rollback()
        session.close()


def test_add_bucket_invalid_ceiling_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            add_bucket("P8", "P8", 1.5, 0.10, 10, session=session)  # ceiling must be in (0, 1]
    finally:
        session.rollback()
        session.close()


def test_add_bucket_floor_above_ceiling_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            add_bucket("P9", "P9", 0.50, 0.60, 10, session=session)  # floor > ceiling
    finally:
        session.rollback()
        session.close()


def test_update_bucket_partial_update_only_changes_given_fields():
    session = SessionLocal()
    try:
        list_buckets(session=session)
        changed = update_bucket("P4", ceiling_pct=0.80, session=session)
        assert changed is True

        row = session.query(PriorityBucket).filter_by(bucket_key="P4").one()
        assert float(row.ceiling_pct) == pytest.approx(0.80)
        assert float(row.floor_pct) == pytest.approx(0.25)  # untouched
        assert row.display_label == "P4"  # untouched

        entry = session.query(AuditLog).filter_by(field_name="priority_bucket.P4").one()
        assert entry.source == ChangeSource.UI_EDIT.value
    finally:
        session.rollback()
        session.close()


def test_update_bucket_no_actual_change_writes_no_audit_row():
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        p3 = next(b for b in buckets if b["bucket_key"] == "P3")
        changed = update_bucket("P3", ceiling_pct=p3["ceiling_pct"], session=session)
        assert changed is False
        assert session.query(AuditLog).filter_by(field_name="priority_bucket.P3").count() == 0
    finally:
        session.rollback()
        session.close()


def test_update_bucket_rotation_position_collision_rejected():
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        other_position = next(b["rotation_position"] for b in buckets if b["bucket_key"] != "P2")
        with pytest.raises(ValueError):
            update_bucket("P2", rotation_position=other_position, session=session)
    finally:
        session.rollback()
        session.close()


def test_update_bucket_unknown_key_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            update_bucket("P99", ceiling_pct=0.5, session=session)
    finally:
        session.rollback()
        session.close()


def test_remove_bucket_blocked_when_a_vendor_is_tagged_into_it():
    """docs/11 Task 2 guard: can't remove a bucket that vendors are
    currently tagged into — surfaces a clear error, doesn't silently orphan
    the vendor."""
    session = SessionLocal()
    try:
        list_buckets(session=session)
        vendor = session.query(Vendor).filter(Vendor.category == VendorCategory.NORMAL).first()
        vendor.priority_tag = VendorPriorityTag.P3
        session.flush()

        with pytest.raises(ValueError) as exc_info:
            remove_bucket("P3", session=session)
        assert vendor.erp_code in str(exc_info.value)

        # still present — the guard blocked the delete
        assert session.query(PriorityBucket).filter_by(bucket_key="P3").first() is not None
    finally:
        session.rollback()
        session.close()


def test_remove_bucket_allowed_when_no_vendor_tagged_into_it():
    session = SessionLocal()
    try:
        list_buckets(session=session)
        add_bucket("P6", "P6", 0.70, 0.15, 4, session=session)  # "P6" isn't a real VendorPriorityTag member — no vendor can ever be tagged into it

        remove_bucket("P6", session=session)

        assert session.query(PriorityBucket).filter_by(bucket_key="P6").first() is None
        entry = session.query(AuditLog).filter_by(field_name="priority_bucket.P6").order_by(AuditLog.id.desc()).first()
        assert entry.new_value is None
        assert entry.old_value is not None
    finally:
        session.rollback()
        session.close()


def test_remove_bucket_unknown_key_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            remove_bucket("P99", session=session)
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    # ponytail: smallest runnable self-check, no framework/fixtures.
    test_list_buckets_seeds_todays_p2_through_p5_on_first_call()
    test_add_bucket_writes_row_and_audit_log()
    test_add_bucket_duplicate_key_rejected()
    test_add_bucket_duplicate_rotation_position_rejected()
    test_add_bucket_invalid_ceiling_rejected()
    test_add_bucket_floor_above_ceiling_rejected()
    test_update_bucket_partial_update_only_changes_given_fields()
    test_update_bucket_no_actual_change_writes_no_audit_row()
    test_update_bucket_rotation_position_collision_rejected()
    test_update_bucket_unknown_key_rejected()
    test_remove_bucket_blocked_when_a_vendor_is_tagged_into_it()
    test_remove_bucket_allowed_when_no_vendor_tagged_into_it()
    test_remove_bucket_unknown_key_rejected()
    print("all priority_bucket_edits self-checks passed")
