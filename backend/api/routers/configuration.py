"""Configuration router — Model 1 weights, Model 3 thresholds/bucket-target
percentages, and a plain listing of the raw config table."""
from fastapi import APIRouter

from backend.configuration.system_config_edits import (
    update_config_value,
    update_model1_weights,
    update_model3_score_thresholds,
)
from backend.db.models import Config
from backend.db.session import SessionLocal
from backend.models.model1_weighted_scoring.scorer import read_weights
from backend.models.model3_priority_bucket.bucketer import SCORE_THRESHOLD_SPEC, TARGET_PCT_SPEC, _seed_and_read
from backend.shared.enums import ChangeSource

from ..schemas.configuration import (
    BucketPctUpdateRequest,
    BucketPctUpdateResponse,
    ConfigRowOut,
    Model1WeightsOut,
    Model1WeightsUpdateRequest,
    Model1WeightsUpdateResponse,
    Model3ThresholdsAndTargetsOut,
    Model3ThresholdsUpdateRequest,
    Model3ThresholdsUpdateResponse,
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


@router.get("/config/model1-weights", response_model=Model1WeightsOut)
def get_model1_weights():
    # read_weights(session) takes no default — this route owns a plain,
    # short-lived session itself rather than a request-scoped Depends().
    session = SessionLocal()
    try:
        weights = read_weights(session)
        session.commit()  # persist any newly-seeded default rows
        return weights
    finally:
        session.close()


@router.put("/config/model1-weights", response_model=Model1WeightsUpdateResponse)
def put_model1_weights(body: Model1WeightsUpdateRequest):
    changed = update_model1_weights(
        body.aging, body.outstanding, body.consistency, body.trend, source=ChangeSource.UI_EDIT, session=None
    )
    return {"changed": changed}


@router.get("/config/model3-thresholds-and-targets", response_model=Model3ThresholdsAndTargetsOut)
def get_model3_thresholds_and_targets():
    # No read helper existed for these — reusing bucketer.py's own
    # _seed_and_read (its de-facto read helper, same seed-then-read
    # behavior as read_weights above) rather than duplicating that logic.
    session = SessionLocal()
    try:
        thresholds = _seed_and_read(session, SCORE_THRESHOLD_SPEC)
        targets = _seed_and_read(session, TARGET_PCT_SPEC)
        session.commit()
        return {
            "p2_min_score": thresholds["model3.threshold.p2_min_score"],
            "p3_min_score": thresholds["model3.threshold.p3_min_score"],
            "p2_target_pct": targets["model3.target_pct.p2"],
            "p3_target_pct": targets["model3.target_pct.p3"],
            "p4_target_pct": targets["model3.target_pct.p4"],
        }
    finally:
        session.close()


@router.put("/config/model3-thresholds", response_model=Model3ThresholdsUpdateResponse)
def put_model3_thresholds(body: Model3ThresholdsUpdateRequest):
    changed = update_model3_score_thresholds(
        body.p2_threshold, body.p3_threshold, source=ChangeSource.UI_EDIT, session=None
    )
    return {"changed": changed}


@router.put("/config/model3-bucket-pct/{key}", response_model=BucketPctUpdateResponse)
def put_model3_bucket_pct(key: str, body: BucketPctUpdateRequest):
    old_fraction, new_fraction = update_config_value(key, body.value, source=ChangeSource.UI_EDIT, session=None)
    return {"old_fraction": old_fraction, "new_fraction": new_fraction}
