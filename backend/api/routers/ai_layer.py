"""AI layer router — zero-allocation vendor talking scripts (Gemini).

Wraps Gemini/network failures into a clean error, not an unhandled 500. A
missing/placeholder API key raises ValueError from gemini_client, which the
app-level exception_handler already turns into a clean 400 — re-raised here
unchanged so it isn't caught by the generic Exception branch below.
"""
from fastapi import APIRouter, HTTPException

from backend.ai_layer.talking_script import generate_talking_scripts

from ..schemas.ai_layer import TalkingScriptsRequest, TalkingScriptsResponse

router = APIRouter(tags=["ai_layer"])


@router.post("/ai/talking-scripts", response_model=TalkingScriptsResponse)
def post_talking_scripts(body: TalkingScriptsRequest):
    try:
        scripts = generate_talking_scripts(body.vendor_allocations)
    except ValueError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini call failed: {exc}") from exc
    return {"scripts": scripts}
