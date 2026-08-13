"""FastAPI dependency that gates every protected router (wired in via
app.include_router(..., dependencies=[Depends(get_current_user)]) in
backend/api/main.py — see that file for exactly which routers are and
aren't covered).

Session identity lives in an httpOnly cookie (see backend/api/routers/
auth.py's login route for how it's set) — never in localStorage/a
JS-readable value, so a successful XSS on the React app still can't steal
a working session token.
"""
from fastapi import Cookie, HTTPException, status

from backend.auth.security import decode_session_token
from backend.db.models import User
from backend.db.session import SessionLocal

SESSION_COOKIE_NAME = "session_token"


def get_current_user(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)) -> dict:
    """Returns {"username", "display_name", "role"} for the signed-in user.

    401 (not 403) on every failure mode — missing cookie, expired/tampered
    token, or a token for a username that's since been removed from the
    users table — so the frontend's single "not logged in, show the login
    page" handler (client.ts) covers all of them identically.
    """
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not session_token:
        raise unauthorized
    username = decode_session_token(session_token)
    if not username:
        raise unauthorized

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            raise unauthorized
        return {"username": user.username, "display_name": user.display_name, "role": user.role}
    finally:
        session.close()
