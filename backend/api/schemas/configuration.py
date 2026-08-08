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
    category_name: str
    ceiling_pct: float
    floor_pct: float
    rotation_position: int
    deletable: bool


class PriorityBucketCreateRequest(BaseModel):
    bucket_key: str
    display_label: str
    category_name: str
    ceiling_pct: float
    floor_pct: float
    rotation_position: int


class PriorityBucketUpdateRequest(BaseModel):
    display_label: str | None = None
    category_name: str | None = None
    ceiling_pct: float | None = None
    floor_pct: float | None = None
    rotation_position: int | None = None
    new_bucket_key: str | None = None


class PriorityBucketUpdateResponse(BaseModel):
    changed: bool


class PriorityBucketReorderRequest(BaseModel):
    ordered_bucket_keys: list[str]


class PriorityBucketReorderResponse(BaseModel):
    changed: bool
