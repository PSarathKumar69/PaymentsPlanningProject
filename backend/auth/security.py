"""Login-credential task: password hashing + JWT session tokens.

Deliberately small — this app has exactly one (or a small handful of)
named Finance login(s), created once via backend/db/seed_users.py, not a
general-purpose user-management system. bcrypt for hashing (industry
standard, salts automatically, no separate salt column needed); PyJWT for
the session token carried in an httpOnly cookie (see dependencies.py/
backend/api/routers/auth.py) — both free, no external auth provider.

JWT_SECRET_KEY: must be set as a real env var in production (Vercel/AWS —
see docs/12-database.md's DATABASE_URL convention for the pattern this
mirrors). A fixed dev-only fallback is used ONLY when DATABASE_URL is also
unset (i.e. local SQLite dev/tests) — same "DATABASE_URL set = production"
signal backend/db/session.py already keys off. Set for real in prod, this
raises loudly instead of silently signing tokens with a guessable secret.
"""
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

JWT_ALGORITHM = "HS256"
# Short-lived by default (a shared browser on someone's desk shouldn't stay
# logged in forever); "Remember me" on the login page extends this — see
# create_session_token()'s remember param and auth.py's login route.
DEFAULT_TOKEN_TTL = timedelta(hours=12)
REMEMBER_ME_TOKEN_TTL = timedelta(days=30)

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not JWT_SECRET_KEY:
    if os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable must be set when DATABASE_URL "
            "(production/Postgres) is configured — refusing to sign login "
            "sessions with a guessable default outside local dev."
        )
    # Local SQLite dev/tests only: fixed so a test run's DB re-import (see
    # backend/api/test_api.py's _fresh_app()) doesn't invalidate a token
    # minted moments earlier under a different random value.
    JWT_SECRET_KEY = "dev-only-insecure-secret-do-not-use-in-production"


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash — never let a bad stored value 500 the
        # login route; treat it the same as "wrong password."
        return False


def create_session_token(username: str, remember: bool = False) -> str:
    ttl = REMEMBER_ME_TOKEN_TTL if remember else DEFAULT_TOKEN_TTL
    expires_at = datetime.now(timezone.utc) + ttl
    return jwt.encode({"sub": username, "exp": expires_at}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> str | None:
    """Returns the username the token was issued for, or None if the token
    is missing, expired, or was tampered with/signed by a different secret."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("sub")


def create_user(session, username: str, password: str, display_name: str | None = None, role: str = "Finance Admin"):
    """Shared by backend/db/seed_users.py (the real Pankaj Kalra login) and
    every test fixture that needs a throwaway authenticated user (per-file
    `client` fixtures across backend/api/test_*.py, backend/validation/) —
    one hashing code path, never duplicated ad hoc in a test file."""
    from backend.db.models import User

    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name or username,
        role=role,
    )
    session.add(user)
    session.commit()
    return user
