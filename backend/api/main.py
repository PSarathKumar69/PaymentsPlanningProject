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
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from backend.auth.dependencies import get_current_user
from backend.ingestion.ai_column_mapper import AIMappingUnavailableError

from .routers import (
    ai_layer,
    analytics,
    audit_log,
    auth,
    calendar,
    configuration,
    ingestion,
    master_data,
    new_model_2,
    payments,
    plan_allocations,
    plan_runs,
    vendors,
    weekly_planning,
)

app = FastAPI(title="Vendor Payment Planning & Prioritization Automation")

# Login-credential task: every real data route below requires a signed-in
# Finance user (see backend/auth/). /auth/* itself is deliberately excluded
# — /login is how the cookie gets minted in the first place, and /logout
# and /me both need to run even when there's no valid session yet.
_require_login = [Depends(get_current_user)]

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.exception_handler(ValueError)
def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(AIMappingUnavailableError)
def ai_mapping_unavailable_handler(request: Request, exc: AIMappingUnavailableError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(auth.router)

app.include_router(vendors.router, dependencies=_require_login)
app.include_router(calendar.router, dependencies=_require_login)
app.include_router(new_model_2.router, dependencies=_require_login)
app.include_router(weekly_planning.router, dependencies=_require_login)
app.include_router(payments.router, dependencies=_require_login)
app.include_router(plan_allocations.router, dependencies=_require_login)
app.include_router(plan_runs.router, dependencies=_require_login)
app.include_router(configuration.router, dependencies=_require_login)
app.include_router(ingestion.router, dependencies=_require_login)
app.include_router(master_data.router, dependencies=_require_login)
app.include_router(audit_log.router, dependencies=_require_login)
app.include_router(ai_layer.router, dependencies=_require_login)
app.include_router(analytics.router, dependencies=_require_login)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/test-ui", include_in_schema=False)
def serve_test_ui():
    return FileResponse(os.path.join(STATIC_DIR, "test_ui.html"))


# Docker task: on Vercel the frontend is its own separate "service"
# (vercel.json's rewrites), never served by this app — but a single Docker
# image has no second service to hand it off to, so this app serves the
# built React app (frontend/npm run build's dist/) itself when that
# directory is present. Registered last on purpose: every route/router
# above (including /test-ui and the API routers) is matched first, so
# nothing here can ever shadow a real API path — this only ever catches
# whatever's left over, exactly like vercel.json's own final `/(.*)  ->
# frontend` rewrite rule.
FRONTEND_DIST_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(FRONTEND_DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST_DIR, "assets")), name="frontend-assets")

    # Bug fix (found while regression-testing the Unique-column fix):
    # a GET-only catch-all doesn't actually keep "nothing here can ever
    # shadow a real API path" true for REMOVED/mistyped API paths on other
    # methods — Starlette's router treats "the path pattern matches, the
    # method doesn't" as 405 Method Not Allowed, not 404, regardless of
    # what serve_frontend's body does (the mismatch is decided before the
    # function ever runs). That's how e.g. the old POST /ai/ask, gone from
    # ai_layer.py, started reporting 405 instead of 404 the moment this
    # catch-all was added. Fix: declare every method on this route (so
    # Starlette never sees a method mismatch here to begin with), and
    # decide 404-vs-serve inside the function instead — a path whose first
    # segment matches a real router's own prefix, or any non-GET request,
    # 404s like a genuinely unmatched route should.
    _KNOWN_API_PREFIXES = {
        r.path.strip("/").split("/")[0]
        for r in app.routes
        if getattr(r, "path", None) not in (None, "/", "/{full_path:path}") and r.path.strip("/")
    }

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def serve_frontend(full_path: str, request: Request):
        if request.method != "GET" or full_path.split("/")[0] in _KNOWN_API_PREFIXES:
            raise HTTPException(status_code=404)
        # A refresh on any "page" of the app (it's a single-page app with no
        # real client-side routes today, but this keeps working if that
        # changes) still returns the same index.html, same as a normal SPA
        # deployment — the React app itself decides what to render.
        return FileResponse(os.path.join(FRONTEND_DIST_DIR, "index.html"))
