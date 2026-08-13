"""Tests against the real ingested database (backend/db/app.db) — no synthetic
data, always session.rollback()'d, never committed. Same convention as
backend/configuration/test_system_config_edits.py."""
import pytest

from backend.configuration.priority_bucket_edits import (
    add_bucket,
    list_buckets,
    remove_bucket,
    reorder_buckets,
    update_bucket,
)
from backend.db.models import AuditLog, PriorityBucket, Vendor
from backend.db.session import SessionLocal
from backend.models.new_model_2.allocator import generate_plan
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
        add_bucket("P6", "P6", 0.70, 0.15, 4, "Contractor", source=ChangeSource.UI_EDIT, session=session)

        row = session.query(PriorityBucket).filter_by(bucket_key="P6").one()
        assert row.display_label == "P6"
        assert row.category_name == "Contractor"
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
            add_bucket("P2", "Duplicate P2", 0.50, 0.10, 50, "Normal", session=session)
    finally:
        session.rollback()
        session.close()


def test_add_bucket_duplicate_rotation_position_rejected():
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        taken_position = buckets[0]["rotation_position"]
        with pytest.raises(ValueError):
            add_bucket("P7", "P7", 0.50, 0.10, taken_position, "Contractor", session=session)
    finally:
        session.rollback()
        session.close()


def test_add_bucket_invalid_ceiling_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            add_bucket("P8", "P8", 1.5, 0.10, 10, "Contractor", session=session)  # ceiling must be in (0, 1]
    finally:
        session.rollback()
        session.close()


def test_add_bucket_floor_above_ceiling_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            add_bucket("P9", "P9", 0.50, 0.60, 10, "Contractor", session=session)  # floor > ceiling
    finally:
        session.rollback()
        session.close()


def test_add_bucket_empty_category_name_rejected():
    session = SessionLocal()
    try:
        with pytest.raises(ValueError):
            add_bucket("P10", "P10", 0.50, 0.10, 10, "   ", session=session)
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


def test_update_bucket_category_name_change_cascades_to_vendors_tagged_into_it():
    """Renaming THIS row's category_name relabels only the vendors tagged
    with THIS row's own bucket_key — not every vendor sharing the old
    category label via a sibling row (e.g. P2/P3/P4 all "Normal")."""
    session = SessionLocal()
    try:
        list_buckets(session=session)
        vendor = session.query(Vendor).first()
        vendor.priority_tag = "P3"
        vendor.category = VendorCategory.NORMAL.value
        session.flush()

        update_bucket("P3", category_name="Priority Normal", session=session)

        session.refresh(vendor)
        assert vendor.category == "Priority Normal"
        row = session.query(PriorityBucket).filter_by(bucket_key="P3").one()
        assert row.category_name == "Priority Normal"
        # P2's own row/vendors are untouched — cascade is row-scoped, not
        # every row sharing the old "Normal" label.
        p2_row = session.query(PriorityBucket).filter_by(bucket_key="P2").one()
        assert p2_row.category_name == VendorCategory.NORMAL.value
    finally:
        session.rollback()
        session.close()


def test_update_bucket_new_bucket_key_cascades_to_vendors_tagged_into_it():
    session = SessionLocal()
    try:
        list_buckets(session=session)
        vendor = session.query(Vendor).first()
        vendor.category = VendorCategory.INACTIVE.value
        vendor.priority_tag = "P5"
        session.flush()

        changed = update_bucket("P5", new_bucket_key="P5X", session=session)
        assert changed is True

        session.refresh(vendor)
        assert vendor.priority_tag == "P5X"
        assert session.query(PriorityBucket).filter_by(bucket_key="P5").first() is None
        assert session.query(PriorityBucket).filter_by(bucket_key="P5X").first() is not None
    finally:
        session.rollback()
        session.close()


def test_update_bucket_new_bucket_key_already_exists_rejected():
    session = SessionLocal()
    try:
        list_buckets(session=session)
        with pytest.raises(ValueError):
            update_bucket("P2", new_bucket_key="P3", session=session)
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
        add_bucket("P6", "P6", 0.70, 0.15, 4, "Contractor", session=session)  # "P6" isn't a real VendorPriorityTag member — no vendor can ever be tagged into it

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


def test_remove_bucket_blocked_when_a_vendor_is_categorized_into_it_but_not_tagged():
    """The extended guard (this task): a vendor CATEGORIZED into a bucket's
    category_name (but not necessarily carrying this exact row's own
    bucket_key as its priority_tag) still blocks removal — Vendor.category
    and Vendor.priority_tag are now both plain strings, so a vendor can be
    categorized into a custom category without its tag matching yet."""
    session = SessionLocal()
    try:
        list_buckets(session=session)
        add_bucket("P6", "P6", 0.70, 0.15, 4, "Contractor", session=session)
        vendor = session.query(Vendor).first()
        vendor.category = "Contractor"
        vendor.priority_tag = None
        session.flush()

        with pytest.raises(ValueError) as exc_info:
            remove_bucket("P6", session=session)
        assert vendor.erp_code in str(exc_info.value)
    finally:
        session.rollback()
        session.close()


def test_reorder_buckets_persists_new_order():
    """P0/P1 edit-lock removed (2026-08-07 task, see reorder_buckets()'s own
    docstring): this test used to reorder only the non-pinned rows, leaving
    P0/P1 fixed first — that restriction was deliberately reversed. P0/P1
    are ordinary reorderable rows now, exactly like every other bucket, and
    reorder_buckets() itself requires the payload to be EVERY existing
    bucket_key (see the sibling rejection test below) — so this reverses
    the FULL key list, P0/P1 included, and confirms the DB ends up in
    exactly that order with no special-cased pinned positions."""
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        keys = [b["bucket_key"] for b in buckets]
        new_order = list(reversed(keys))

        changed = reorder_buckets(new_order, session=session)
        assert changed is True

        rows = session.query(PriorityBucket).order_by(PriorityBucket.rotation_position).all()
        assert [r.bucket_key for r in rows] == new_order  # P0/P1 moved too — no longer pinned to the front
        entry = session.query(AuditLog).filter_by(field_name="priority_bucket_order").order_by(AuditLog.id.desc()).first()
        assert entry is not None
        assert entry.new_value == " -> ".join(new_order)
    finally:
        session.rollback()
        session.close()


def test_reorder_buckets_rejects_a_payload_missing_pinned_keys():
    """Real bug found and fixed (this task, Sarath's Config-module ask):
    this test used to assert the OPPOSITE of reorder_buckets()'s actual,
    documented current behavior — it asserted P0/P1 must NEVER appear in
    the payload, when the 2026-08-07 "P0/P1 edit-lock removed" task made
    them ordinary reorderable rows and reorder_buckets() now requires
    ordered_bucket_keys to be EVERY existing bucket_key, P0/P1 included
    (rejects anything else — see the `set(ordered_bucket_keys) != set(rows)`
    check). The behavior was already correct; only this test's assumption
    was stale — confirmed by running the OLD version of this test against
    today's code: it failed with reorder_buckets() actually raising
    ValueError("...must be exactly the existing bucket keys...") because
    P0/P1 were MISSING from its payload, the mirror image of what it
    expected to see rejected. This version pins down the real current
    rule: a payload that omits the pinned keys is rejected, same as
    omitting any other key (test_reorder_buckets_wrong_key_set_rejected
    below covers the general case; this one specifically exercises P0/P1)."""
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        keys_missing_pinned = [b["bucket_key"] for b in buckets if b["deletable"]]  # omits P0/P1 on purpose
        with pytest.raises(ValueError):
            reorder_buckets(keys_missing_pinned, session=session)
    finally:
        session.rollback()
        session.close()


def test_reorder_buckets_wrong_key_set_rejected():
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        keys = [b["bucket_key"] for b in buckets]
        with pytest.raises(ValueError):
            reorder_buckets(keys[:-1], session=session)  # missing one real key
    finally:
        session.rollback()
        session.close()


def test_reorder_buckets_changes_generate_plans_actual_cut_order():
    """The end-to-end proof this task's own checklist asks for: reordering
    rows must change WHICH bucket generate_plan() actually cuts first under
    a shortfall — not just persist a new row order with no behavioral
    effect."""
    session = SessionLocal()
    try:
        buckets = list_buckets(session=session)
        # reorder_buckets() now requires every existing bucket_key in the
        # payload (P0/P1 edit-lock removed, 2026-08-07 task) — so the full
        # set, not just the non-pinned ("deletable") ones, must be reversed.
        # Only P2/P3 carry real vendors in this test's eligible set, so
        # moving P0/P1 around doesn't change their relative cut order.
        keys = [b["bucket_key"] for b in buckets]

        normal_vendors = session.query(Vendor).filter(Vendor.category == VendorCategory.NORMAL).limit(2).all()
        assert len(normal_vendors) >= 2, "fixture DB needs at least 2 Normal vendors for this test"
        v_p2, v_p3 = normal_vendors[0], normal_vendors[1]
        v_p2.category = VendorCategory.NORMAL.value
        v_p2.priority_tag = "P2"
        v_p2.opening_balance = 1_000_000.0
        v_p2.paid_so_far_this_month = 0.0
        v_p3.category = VendorCategory.NORMAL.value
        v_p3.priority_tag = "P3"
        v_p3.opening_balance = 1_000_000.0
        v_p3.paid_so_far_this_month = 0.0
        session.flush()

        # A shortfall tight enough to force real cutting between just these
        # two vendors' full required amounts, funding only P0/P1 first.
        available = float(v_p2.opening_balance + v_p3.opening_balance) * 0.5
        eligible_ids = {v_p2.id, v_p3.id}

        result_p2_first = generate_plan(available, session=session, eligible_vendor_ids=eligible_ids)
        pct_before = result_p2_first["bucket_pct"]

        reorder_buckets(list(reversed(keys)), session=session, source=ChangeSource.UI_EDIT)
        result_p3_first = generate_plan(available, session=session, eligible_vendor_ids=eligible_ids)
        pct_after = result_p3_first["bucket_pct"]

        # P2 was cut-last (highest priority) before, P3 cut-last after —
        # the reorder must have actually flipped which one loses more.
        assert pct_before["P2"] >= pct_before["P3"]
        assert pct_after["P3"] >= pct_after["P2"]
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
