"""Login-credential task: create (or reset the password for) one named
Finance login. There is no signup page/API on purpose — this script IS the
"admin backend" for who's allowed to sign in, run by hand against whichever
DATABASE_URL you're pointed at (unset = local SQLite dev DB).

Usage:
    python -m backend.db.seed_users "<username>" "<password>" ["<display name>"] ["<role>"]

Examples (placeholders only — never put a real credential in this file;
it's committed to source control, and this docstring is not the place to
record anyone's actual login):
    python -m backend.db.seed_users "Jane Doe" "<new-password>" "Jane Doe" "Finance Admin"

    # Against production Postgres:
    DATABASE_URL=postgres://...connection-string... \\
        python -m backend.db.seed_users "Jane Doe" "<new-password>" "Jane Doe"

Idempotent by design, not append-only: re-running with the same username
resets that user's password/display name/role in place (a real "forgot
password" flow, just admin-run instead of self-service) rather than
erroring or creating a duplicate row.
"""
import sys

from backend.auth.security import hash_password
from backend.db.models import User
from backend.db.session import SessionLocal


def seed_user(username: str, password: str, display_name: str | None = None, role: str = "Finance Admin") -> None:
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        if user is None:
            user = User(username=username, password_hash=hash_password(password), display_name=display_name or username, role=role)
            session.add(user)
            action = "Created"
        else:
            user.password_hash = hash_password(password)
            user.display_name = display_name or user.display_name
            user.role = role
            action = "Updated"
        session.commit()
        print(f"{action} login for '{username}' (display name: {user.display_name}, role: {user.role}).")
    finally:
        session.close()


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    username, password = sys.argv[1], sys.argv[2]
    display_name = sys.argv[3] if len(sys.argv) > 3 else None
    role = sys.argv[4] if len(sys.argv) > 4 else "Finance Admin"
    seed_user(username, password, display_name, role)


if __name__ == "__main__":
    main()
