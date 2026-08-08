"""Request/response schemas for the AI layer's talking-script router."""
from typing import Any, Literal

from pydantic import BaseModel


class TalkingScriptsRequest(BaseModel):
    vendor_allocations: list[dict[str, Any]]  # a prior generate_plan()'s "allocations" records


class TalkingScript(BaseModel):
    vendor_id: int
    erp_code: str
    vendor_name: str
    script_text: str


class TalkingScriptsResponse(BaseModel):
    scripts: list[TalkingScript]


class VendorTalkingPointsRequest(BaseModel):
    vendor_id: int
    # Talking/Email toggle (docs: AI screen revamp) — "talking" (spoken phone
    # script) or "email" (written email), same fact pack either way. Default
    # keeps every existing caller's behavior unchanged.
    format: Literal["talking", "email"] = "talking"


class VendorTalkingPointsResponse(BaseModel):
    vendor_id: int
    erp_code: str
    vendor_name: str
    category: str
    priority_tag: str | None
    status: str
    required_amount: float
    allocated_amount: float
    cut_from_full: bool
    aging_bucket: str | None
    # Holds whichever format was requested (talking script or email body) —
    # not a distinctly-named field per format, since a caller only ever asks
    # for "the current tab's text" one format at a time (see companion.py's
    # `format` kwarg) and never needs both simultaneously in one response.
    script_text: str
