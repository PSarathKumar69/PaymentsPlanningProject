"""Request/response schemas for the AI layer's talking-script router."""
from typing import Any

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
