"""Configuration router — a plain listing of the raw config table, plus New
Model 2's own priority-bucket structure (ceiling/floor/rotation order).
Model 1 weights and Model 3 thresholds/bucket-target endpoints were removed
once New Model 2 became the sole model — their config values had no
remaining caller."""
from fastapi import APIRouter

from backend.configuration.priority_bucket_edits import add_bucket, list_buckets, remove_bucket, update_bucket
from backend.db.models import Config
from backend.db.session import SessionLocal
from backend.shared.enums import ChangeSource

from ..schemas.configuration import (
    ConfigRowOut,
    PriorityBucketCreateRequest,
    PriorityBucketOut,
    PriorityBucketUpdateRequest,
    PriorityBucketUpdateResponse,
)

router = APIRouter(tags=["configuration"])


@router.get("/config", response_model=list[ConfigRowOut])
def list_config():
    session = SessionLocal()
    try:
        rows = session.query(Config).order_by(Config.key).all()
        return [{"id": r.id, "key": r.key, "value": r.value, "description": r.description} for r in rows]
    finally:
        session.close()


# ---- New Model 2 priority-bucket structure (docs/11 Task 2 Part C) --------
# The only New-Model-2-specific, non-vendor-specific things Configuration
# owns (docs/11's scope decision): which buckets exist, their ceiling/floor,
# and their rotation order — not any vendor's own category/tag (those moved
# to the Planning screen, see docs/11 Task 2 Part A/B).


@router.get("/config/priority-buckets", response_model=list[PriorityBucketOut])
def get_priority_buckets():
    return list_buckets()


@router.post("/config/priority-buckets", response_model=PriorityBucketOut)
def post_priority_bucket(body: PriorityBucketCreateRequest):
    add_bucket(
        body.bucket_key,
        body.display_label,
        body.ceiling_pct,
        body.floor_pct,
        body.rotation_position,
        source=ChangeSource.UI_EDIT,
    )
    return {
        "bucket_key": body.bucket_key,
        "display_label": body.display_label,
        "ceiling_pct": body.ceiling_pct,
        "floor_pct": body.floor_pct,
        "rotation_position": body.rotation_position,
    }


@router.put("/config/priority-buckets/{bucket_key}", response_model=PriorityBucketUpdateResponse)
def put_priority_bucket(bucket_key: str, body: PriorityBucketUpdateRequest):
    changed = update_bucket(
        bucket_key,
        display_label=body.display_label,
        ceiling_pct=body.ceiling_pct,
        floor_pct=body.floor_pct,
        rotation_position=body.rotation_position,
        source=ChangeSource.UI_EDIT,
    )
    return {"changed": changed}


@router.delete("/config/priority-buckets/{bucket_key}")
def delete_priority_bucket(bucket_key: str):
    remove_bucket(bucket_key, source=ChangeSource.UI_EDIT)
    return {"removed": bucket_key}
