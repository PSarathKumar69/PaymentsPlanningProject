"""Request/response schemas for the configuration router."""
from pydantic import BaseModel


class ConfigRowOut(BaseModel):
    id: int
    key: str
    value: str
    description: str | None


class Model1WeightsOut(BaseModel):
    aging: float
    outstanding: float
    consistency: float
    trend: float


class Model1WeightsUpdateRequest(BaseModel):
    aging: float
    outstanding: float
    consistency: float
    trend: float


class Model1WeightsUpdateResponse(BaseModel):
    changed: list[str]


class Model3ThresholdsAndTargetsOut(BaseModel):
    p2_min_score: float
    p3_min_score: float
    p2_target_pct: float
    p3_target_pct: float
    p4_target_pct: float


class Model3ThresholdsUpdateRequest(BaseModel):
    p2_threshold: float
    p3_threshold: float


class Model3ThresholdsUpdateResponse(BaseModel):
    changed: list[str]


class BucketPctUpdateRequest(BaseModel):
    value: float


class BucketPctUpdateResponse(BaseModel):
    old_fraction: float | None
    new_fraction: float
