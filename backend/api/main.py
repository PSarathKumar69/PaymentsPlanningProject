"""FastAPI app — thin wrapper over the deterministic backend (CLAUDE.md rule
2: suggestion-only, no real payment/bank API is ever called here).

Every route below calls its underlying function exactly as it exists today,
passing session=None so the function owns/commits/closes its own session
per call — same convention used everywhere else in this codebase. No
request-scoped session via Depends(): that's a deliberate choice, not an
oversight (see the routers for functions like read_weights() that require
a session — those own a short-lived plain SessionLocal() directly instead).
"""
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .routers import (
    ai_layer,
    audit_log,
    calendar,
    configuration,
    ingestion,
    master_data,
    new_model_2,
    payments,
    plan_allocations,
    plan_runs,
    rollover,
    vendors,
    weekly_planning,
)

app = FastAPI(title="Vendor Payment Planning & Prioritization Automation")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.exception_handler(ValueError)
def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(vendors.router)
app.include_router(calendar.router)
app.include_router(new_model_2.router)
app.include_router(weekly_planning.router)
app.include_router(payments.router)
app.include_router(plan_allocations.router)
app.include_router(plan_runs.router)
app.include_router(configuration.router)
app.include_router(ingestion.router)
app.include_router(master_data.router)
app.include_router(rollover.router)
app.include_router(audit_log.router)
app.include_router(ai_layer.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def serve_test_ui():
    return FileResponse(os.path.join(STATIC_DIR, "test_ui.html"))
