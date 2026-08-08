"""Priority-bucket CRUD — Configuration module, New Model 2 bucket
structure only (docs/11-configuration-module.md Task 2 Part C). Lets
Finance add/edit/remove a priority bucket itself (not just tweak an
existing bucket's ceiling/floor numbers) — backs
backend/models/new_model_2/allocator.py's _seed_and_read_buckets(), which
reads the same PriorityBucket table these functions write to.

Same conventions as every other Configuration edit (docs/11): every change
is one append-only audit_log row; session-ownership contract matches
system_config_edits.py (owns its own session by default, commits; a
caller-supplied session is flushed-only, left to the caller to commit).
"""
from datetime import datetime

from backend.db.models import AuditLog, PriorityBucket, Vendor
from backend.db.session import SessionLocal
from backend.models.new_model_2.allocator import _seed_and_read_buckets
from backend.shared.enums import ChangeSource


def _describe(display_label, ceiling_pct, floor_pct, rotation_position, category_name=None):
    base = f"{display_label} (ceiling={ceiling_pct}, floor={floor_pct}, position={rotation_position}"
    return f"{base}, category={category_name})" if category_name else f"{base})"


def _validate_pct(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 1):
        raise ValueError(f"{name} must be a fraction in (0, 1], got {value!r}")


def _validate_bucket_fields(display_label, ceiling_pct, floor_pct, rotation_position, category_name):
    if not isinstance(display_label, str) or not display_label.strip():
        raise ValueError(f"display_label must be a non-empty string, got {display_label!r}")
    if not isinstance(category_name, str) or not category_name.strip():
        raise ValueError(f"category_name must be a non-empty string, got {category_name!r}")
    _validate_pct("ceiling_pct", ceiling_pct)
    _validate_pct("floor_pct", floor_pct)
    if floor_pct > ceiling_pct:
        raise ValueError(f"floor_pct ({floor_pct}) must not exceed ceiling_pct ({ceiling_pct})")
    # P0/P1-ceiling task: rotation_position may be negative now (P0/P1 are
    # pinned at -2/-1, ahead of every reorderable row's 0..N-1) — only
    # integer-ness and uniqueness (checked by callers) matter, not sign.
    if isinstance(rotation_position, bool) or not isinstance(rotation_position, int):
        raise ValueError(f"rotation_position must be an integer, got {rotation_position!r}")


def list_buckets(session=None):
    """Every bucket row, ordered by rotation_position — seeds today's
    P2/P3/P4/P5 defaults first if the table is still completely empty (a
    fresh DB that's never called generate_plan() or this CRUD before)."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        _seed_and_read_buckets(session)
        if owns_session:
            session.commit()
        rows = session.query(PriorityBucket).order_by(PriorityBucket.rotation_position).all()
        return [
            {
                "bucket_key": r.bucket_key,
                "display_label": r.display_label,
                "category_name": r.category_name,
                "ceiling_pct": float(r.ceiling_pct),
                "floor_pct": float(r.floor_pct),
                "rotation_position": r.rotation_position,
                # Pinned-role task: the frontend greys out/hides Remove for a
                # row it can never delete, keyed off this field — never a
                # hardcoded "P0"/"P1" bucket_key check of its own.
                "deletable": r.pinned_role is None,
            }
            for r in rows
        ]
    finally:
        if owns_session:
            session.close()


def add_bucket(
    bucket_key,
    display_label,
    ceiling_pct,
    floor_pct,
    rotation_position,
    category_name,
    source=ChangeSource.UI_EDIT,
    session=None,
):
    """Adds a brand-new bucket row — this is also how Finance adds a
    brand-new vendor CATEGORY (Configuration-tab-rebuild task): a
    bucket_key that isn't already a VendorPriorityTag enum member (P2-P5
    today) works immediately for allocator.py's ceiling/cutting arithmetic
    and for the untagged-vendor defensive fallback (bucket_order[-1]), AND
    a real vendor can now be tagged/categorized into it on purpose —
    Vendor.category/priority_tag are plain String columns (see
    backend/db/models.py), not fixed DB Enums, so no enum-member addition
    is needed on this path anymore.
    """
    if not isinstance(bucket_key, str) or not bucket_key.strip():
        raise ValueError(f"bucket_key must be a non-empty string, got {bucket_key!r}")
    bucket_key = bucket_key.strip()
    category_name = category_name.strip() if isinstance(category_name, str) else category_name
    _validate_bucket_fields(display_label, ceiling_pct, floor_pct, rotation_position, category_name)

    owns_session = session is None
    session = session or SessionLocal()
    try:
        if session.query(PriorityBucket).filter_by(bucket_key=bucket_key).first() is not None:
            raise ValueError(f"a bucket with key {bucket_key!r} already exists")
        if session.query(PriorityBucket).filter_by(rotation_position=rotation_position).first() is not None:
            raise ValueError(f"rotation_position {rotation_position} is already taken by another bucket")

        session.add(
            PriorityBucket(
                bucket_key=bucket_key,
                display_label=display_label,
                category_name=category_name,
                ceiling_pct=ceiling_pct,
                floor_pct=floor_pct,
                rotation_position=rotation_position,
            )
        )
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=None,
                field_name=f"priority_bucket.{bucket_key}",
                old_value=None,
                new_value=_describe(display_label, ceiling_pct, floor_pct, rotation_position, category_name),
                source=source.value,
            )
        )
        session.flush()
        if owns_session:
            session.commit()
        return bucket_key
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def update_bucket(
    bucket_key,
    display_label=None,
    ceiling_pct=None,
    floor_pct=None,
    rotation_position=None,
    category_name=None,
    new_bucket_key=None,
    source=ChangeSource.UI_EDIT,
    session=None,
):
    """Partial update — any field left as None (not passed) keeps its
    current value. Returns True if anything actually changed (an
    audit_log row is written only then).

    `new_bucket_key` (Configuration-tab-rebuild task): Priority Tag is now
    Finance-editable text, not a fixed key — renaming it cascades onto
    every vendor currently tagged with the OLD key
    (`Vendor.priority_tag`), written in the same transaction as the bucket
    row's own update so a partial rename can never happen. Likewise,
    changing `category_name` cascades onto vendors tagged into THIS row's
    own bucket_key whose category still matches the OLD name — scoped to
    this row, not every row sharing that category name (docs: several
    rows, e.g. P2/P3/P4, can share one category label; renaming one tier's
    label shouldn't silently relabel its siblings too).

    P0/P1 edit-lock removed (2026-08-07 task, reversing the earlier
    P0/P1-ceiling task's restriction): bucket_key/category_name/
    rotation_position are now Finance-editable for P0/P1 exactly like
    every other row. Safe to rename now (pinned-role task, same date):
    allocator.py's guaranteed-funding-tier detection keys off this row's
    own `pinned_role` column ("must_pay"/"commitment"), not its bucket_key
    text — a rename here doesn't move the row's vendors out of the
    guaranteed tier, only its label changes. `pinned_role` itself is never
    touched by this function; it's permanent once seeded.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        row = session.query(PriorityBucket).filter_by(bucket_key=bucket_key).first()
        if row is None:
            raise ValueError(f"no bucket with key {bucket_key!r}")

        new_display_label = row.display_label if display_label is None else display_label
        new_ceiling = float(row.ceiling_pct) if ceiling_pct is None else ceiling_pct
        new_floor = float(row.floor_pct) if floor_pct is None else floor_pct
        new_position = row.rotation_position if rotation_position is None else rotation_position
        new_category_name = row.category_name if category_name is None else category_name.strip()
        new_key = bucket_key if new_bucket_key is None else new_bucket_key.strip()
        _validate_bucket_fields(new_display_label, new_ceiling, new_floor, new_position, new_category_name)
        if not new_key:
            raise ValueError("new_bucket_key must be a non-empty string")

        if (
            new_position != row.rotation_position
            and session.query(PriorityBucket)
            .filter(PriorityBucket.rotation_position == new_position, PriorityBucket.bucket_key != bucket_key)
            .first()
            is not None
        ):
            raise ValueError(f"rotation_position {new_position} is already taken by another bucket")
        if (
            new_key != bucket_key
            and session.query(PriorityBucket).filter(PriorityBucket.bucket_key == new_key).first() is not None
        ):
            raise ValueError(f"a bucket with key {new_key!r} already exists")

        old_description = _describe(
            row.display_label, float(row.ceiling_pct), float(row.floor_pct), row.rotation_position, row.category_name
        )
        new_description = _describe(new_display_label, new_ceiling, new_floor, new_position, new_category_name)
        changed = old_description != new_description or new_key != bucket_key

        old_category_name = row.category_name
        row.display_label = new_display_label
        row.ceiling_pct = new_ceiling
        row.floor_pct = new_floor
        row.rotation_position = new_position
        row.category_name = new_category_name
        row.bucket_key = new_key

        if new_key != bucket_key:
            session.query(Vendor).filter(Vendor.priority_tag == bucket_key).update(
                {Vendor.priority_tag: new_key}, synchronize_session=False
            )
        if new_category_name != old_category_name:
            session.query(Vendor).filter(
                Vendor.priority_tag == new_key, Vendor.category == old_category_name
            ).update({Vendor.category: new_category_name}, synchronize_session=False)

        if changed:
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=None,
                    field_name=f"priority_bucket.{bucket_key}",
                    old_value=old_description,
                    new_value=new_description + (f" [key -> {new_key}]" if new_key != bucket_key else ""),
                    source=source.value,
                )
            )
        session.flush()
        if owns_session:
            session.commit()
        return changed
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def remove_bucket(bucket_key, source=ChangeSource.UI_EDIT, session=None):
    """Guard (docs/11 Task 2 — "surface a clear error, don't silently
    orphan vendors"): refuses to remove a bucket that any vendor is
    currently tagged OR categorized into — checked against both
    `Vendor.priority_tag` (this row's own bucket_key) and `Vendor.category`
    (this row's category_name), now that both are plain String columns
    rather than fixed DB Enums (a vendor can be categorized into a custom
    category without necessarily carrying this exact row's own tag).

    Pinned-role task (2026-08-07): a row with `pinned_role` set (Must
    Pay/Commitment's guaranteed-funding tier, however Finance has renamed
    it) can never be removed, in-use or not — deleting it would remove the
    actual funding guarantee, not just relabel it, unlike rename/reorder
    which stay purely cosmetic. This is the one guard the 2026-08-07
    edit-lock-removal task deliberately kept.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        row = session.query(PriorityBucket).filter_by(bucket_key=bucket_key).first()
        if row is None:
            raise ValueError(f"no bucket with key {bucket_key!r}")
        if row.pinned_role is not None:
            raise ValueError(
                f"cannot remove bucket {bucket_key!r} — it is a pinned guaranteed-funding bucket "
                f"({row.pinned_role}); rename or reorder it instead"
            )

        in_use = (
            session.query(Vendor)
            .filter((Vendor.priority_tag == bucket_key) | (Vendor.category == row.category_name))
            .first()
        )
        if in_use is not None:
            raise ValueError(
                f"cannot remove bucket {bucket_key!r} — at least one vendor "
                f"(e.g. {in_use.erp_code}) is currently tagged/categorized into it; retag those vendors first"
            )

        old_description = _describe(
            row.display_label, float(row.ceiling_pct), float(row.floor_pct), row.rotation_position, row.category_name
        )
        session.delete(row)
        session.add(
            AuditLog(
                timestamp=datetime.now(),
                vendor_id=None,
                field_name=f"priority_bucket.{bucket_key}",
                old_value=old_description,
                new_value=None,
                source=source.value,
            )
        )
        session.flush()
        if owns_session:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def reorder_buckets(ordered_bucket_keys, source=ChangeSource.UI_EDIT, session=None):
    """Drag-to-reorder (Configuration-tab-rebuild task): row position IS
    cut order now (top = cut last, bottom = cut first) — Rotation Position
    is no longer its own editable field. `ordered_bucket_keys` must be
    every existing bucket_key, in the new desired order.

    P0/P1 edit-lock removed (2026-08-07 task): P0/P1 are no longer pinned
    out of this reorder — they can move anywhere in the sequence like any
    other row. Note this changes their real funding priority (position
    still drives cut order in allocator.py), unlike renaming their
    bucket_key/category_name, which only affects lookup/labeling.

    Two-pass write (temp placeholders, then final 0..N-1): the table's
    `rotation_position` is UNIQUE, so writing final positions one row at a
    time risks a transient collision with whatever row currently holds the
    target position (e.g. moving row A from 3 -> 0 while row B still sits
    at 0).
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        rows = {r.bucket_key: r for r in session.query(PriorityBucket).all()}
        if set(ordered_bucket_keys) != set(rows):
            raise ValueError(
                f"ordered_bucket_keys must be exactly the existing bucket keys {sorted(rows)}, "
                f"got {ordered_bucket_keys!r}"
            )
        old_order = [r.bucket_key for r in sorted(rows.values(), key=lambda r: r.rotation_position)]

        for i, key in enumerate(ordered_bucket_keys):
            rows[key].rotation_position = -(i + 1) - 1000
        session.flush()
        for i, key in enumerate(ordered_bucket_keys):
            rows[key].rotation_position = i
        session.flush()

        changed = old_order != list(ordered_bucket_keys)
        if changed:
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=None,
                    field_name="priority_bucket_order",
                    old_value=" -> ".join(old_order),
                    new_value=" -> ".join(ordered_bucket_keys),
                    source=source.value,
                )
            )
            session.flush()
        if owns_session:
            session.commit()
        return changed
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
