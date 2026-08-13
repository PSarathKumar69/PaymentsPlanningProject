"""Login-credential task: the auth endpoints this app has.

Deliberately NOT included in main.py's blanket
`dependencies=[Depends(get_current_user)]` protection (see main.py) — /login
is how you get the cookie in the first place, and /logout/me/change-password
each do their own pass/fail handling instead of a hard
401-before-the-route-runs block; change-password still requires a valid
session via its own Depends(get_current_user), same pattern /me uses.

Still no signup route and no *self-service* "forgot password" (no email/SMS
flow exists to prove who's asking) — a fixed, small allowlist of named
Finance users, seeded/reset by hand via backend/db/seed_users.py. The one
self-service password-credential route that does exist, change-password,
only ever lets an already-logged-in user replace their OWN password (proof
of identity = the current password itself), matching what Sarath asked for
on the Configuration page in place of a fake "Forgot Password?" link.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Response, status

from backend.auth.dependencies import SESSION_COOKIE_NAME, get_current_user
from backend.auth.security import create_session_token, hash_password, verify_password
from backend.db.models import User
from backend.db.session import SessionLocal

from ..schemas.auth import ChangePasswordRequest, CurrentUserOut, LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])

# Secure cookie flag mirrors backend/db/session.py's own DATABASE_URL-gated
# convention: Postgres configured implies a real (HTTPS) deployment, plain
# SQLite implies local dev, where the browser would silently refuse to ever
# send a Secure cookie back over http://localhost. Explicit COOKIE_SECURE
# env var wins either way, for the rare case that default is wrong.
_default_secure = "true" if os.environ.get("DATABASE_URL") else "false"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", _default_secure).strip().lower() == "true"

_INVALID_CREDENTIALS = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
# Constant-shape hash so a nonexistent username still spends roughly the
# same time as a real (wrong-password) check — cheap to add, avoids the
# login route trivially confirming/denying which usernames exist via
# response timing.
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEWe0000000000000000000000000000000"


@router.post("/login")
def login(payload: LoginRequest, response: Response):
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.username == payload.username).first()
        password_hash = user.password_hash if user else _DUMMY_HASH
        if not verify_password(payload.password, password_hash) or user is None:
            raise _INVALID_CREDENTIALS

        token = create_session_token(user.username, remember=payload.remember)
        max_age = 30 * 24 * 3600 if payload.remember else 12 * 3600
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            max_age=max_age,
            path="/",
        )
        return {"username": user.username, "display_name": user.display_name, "role": user.role}
    finally:
        session.close()


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=CurrentUserOut)
def me(current_user: dict = Depends(get_current_user)):
    return current_user


_MIN_PASSWORD_LENGTH = 6


@router.put("/change-password")
def change_password(payload: ChangePasswordRequest, current_user: dict = Depends(get_current_user)):
    if len(payload.new_password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"New password must be at least {_MIN_PASSWORD_LENGTH} characters.")

    session = SessionLocal()
    try:
        # Re-fetched by username (not trusted from the request body) —
        # current_user is whatever get_current_user's cookie/DB lookup
        # already verified, so this can only ever change the logged-in
        # user's own password, never anyone else's.
        user = session.query(User).filter(User.username == current_user["username"]).first()
        if user is None or not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        user.password_hash = hash_password(payload.new_password)
        session.commit()
        return {"ok": True}
    finally:
        session.close()
