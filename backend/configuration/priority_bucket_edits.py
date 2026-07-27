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
from backend.shared.enums import ChangeSource, VendorPriorityTag


def _describe(display_label, ceiling_pct, floor_pct, rotation_position):
    return f"{display_label} (ceiling={ceiling_pct}, floor={floor_pct}, position={rotation_position})"


def _validate_pct(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 1):
        raise ValueError(f"{name} must be a fraction in (0, 1], got {value!r}")


def _validate_bucket_fields(display_label, ceiling_pct, floor_pct, rotation_position):
    if not isinstance(display_label, str) or not display_label.strip():
        raise ValueError(f"display_label must be a non-empty string, got {display_label!r}")
    _validate_pct("ceiling_pct", ceiling_pct)
    _validate_pct("floor_pct", floor_pct)
    if floor_pct > ceiling_pct:
        raise ValueError(f"floor_pct ({floor_pct}) must not exceed ceiling_pct ({ceiling_pct})")
    if isinstance(rotation_position, bool) or not isinstance(rotation_position, int) or rotation_position < 0:
        raise ValueError(f"rotation_position must be a non-negative integer, got {rotation_position!r}")


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
                "ceiling_pct": float(r.ceiling_pct),
                "floor_pct": float(r.floor_pct),
                "rotation_position": r.rotation_position,
            }
            for r in rows
        ]
    finally:
        if owns_session:
            session.close()


def add_bucket(
    bucket_key, display_label, ceiling_pct, floor_pct, rotation_position, source=ChangeSource.UI_EDIT, session=None
):
    """Adds a brand-new bucket row. NOTE (flagged, not silently papered
    over): a bucket_key that isn't already a VendorPriorityTag enum member
    (P2-P5 today) can be added here and will immediately work for
    allocator.py's ceiling/cutting arithmetic and for the untagged-vendor
    defensive fallback (bucket_order[-1]) — but no real Normal/Inactive
    vendor can actually be *tagged* into it on purpose until
    VendorPriorityTag gains a matching member, since Vendor.priority_tag is
    a real DB Enum column. Adding a bucket Finance can assign vendors into
    directly still needs that one-line enum addition; this function's own
    job (bucket structure/ceiling/floor) has no such limitation.
    """
    if not isinstance(bucket_key, str) or not bucket_key.strip():
        raise ValueError(f"bucket_key must be a non-empty string, got {bucket_key!r}")
    bucket_key = bucket_key.strip()
    _validate_bucket_fields(display_label, ceiling_pct, floor_pct, rotation_position)

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
                new_value=_describe(display_label, ceiling_pct, floor_pct, rotation_position),
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
    source=ChangeSource.UI_EDIT,
    session=None,
):
    """Partial update — any of the four fields left as None (not passed)
    keeps its current value. Returns True if anything actually changed
    (an audit_log row is written only then)."""
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
        _validate_bucket_fields(new_display_label, new_ceiling, new_floor, new_position)

        if (
            new_position != row.rotation_position
            and session.query(PriorityBucket)
            .filter(PriorityBucket.rotation_position == new_position, PriorityBucket.bucket_key != bucket_key)
            .first()
            is not None
        ):
            raise ValueError(f"rotation_position {new_position} is already taken by another bucket")

        old_description = _describe(row.display_label, float(row.ceiling_pct), float(row.floor_pct), row.rotation_position)
        new_description = _describe(new_display_label, new_ceiling, new_floor, new_position)
        changed = old_description != new_description

        row.display_label = new_display_label
        row.ceiling_pct = new_ceiling
        row.floor_pct = new_floor
        row.rotation_position = new_position

        if changed:
            session.add(
                AuditLog(
                    timestamp=datetime.now(),
                    vendor_id=None,
                    field_name=f"priority_bucket.{bucket_key}",
                    old_value=old_description,
                    new_value=new_description,
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
    currently tagged into. Only checked against real VendorPriorityTag
    members (P2-P5 today) — a bucket_key that was never a valid
    VendorPriorityTag member can never have had a vendor tagged into it
    (Vendor.priority_tag is a DB Enum column), so the guard is vacuously
    satisfied for those.
    """
    owns_session = session is None
    session = session or SessionLocal()
    try:
        row = session.query(PriorityBucket).filter_by(bucket_key=bucket_key).first()
        if row is None:
            raise ValueError(f"no bucket with key {bucket_key!r}")

        try:
            tag = VendorPriorityTag(bucket_key)
        except ValueError:
            tag = None
        if tag is not None:
            in_use = session.query(Vendor).filter(Vendor.priority_tag == tag).first()
            if in_use is not None:
                raise ValueError(
                    f"cannot remove bucket {bucket_key!r} — at least one vendor "
                    f"(e.g. {in_use.erp_code}) is currently tagged into it; retag those vendors first"
                )

        old_description = _describe(row.display_label, float(row.ceiling_pct), float(row.floor_pct), row.rotation_position)
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
