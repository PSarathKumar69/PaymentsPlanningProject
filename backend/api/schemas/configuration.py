"""Request/response schemas for the configuration router."""
from pydantic import BaseModel


class ConfigRowOut(BaseModel):
    id: int
    key: str
    value: str
    description: str | None


class PriorityBucketOut(BaseModel):
    bucket_key: str
    display_label: str
    ceiling_pct: float
    floor_pct: float
    rotation_position: int


class PriorityBucketCreateRequest(BaseModel):
    bucket_key: str
    display_label: str
    ceiling_pct: float
    floor_pct: float
    rotation_position: int


class PriorityBucketUpdateRequest(BaseModel):
    display_label: str | None = None
    ceiling_pct: float | None = None
    floor_pct: float | None = None
    rotation_position: int | None = None


class PriorityBucketUpdateResponse(BaseModel):
    changed: bool
