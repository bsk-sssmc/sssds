"""Single-admin password auth.

There's exactly one user. We store a bcrypt hash of their password in
config and a signed-cookie session in the browser. No usernames, no
recovery flow — keep it boring.
"""

from __future__ import annotations

import bcrypt
from itsdangerous import BadSignature, URLSafeSerializer

SESSION_COOKIE = "sssds_session"
SESSION_VALUE = "admin"  # opaque payload; presence == authenticated


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except ValueError:
        return False


def make_serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt="sssds-session")


def issue_cookie(secret: str) -> str:
    return make_serializer(secret).dumps(SESSION_VALUE)


def is_authenticated(secret: str, cookie: str | None) -> bool:
    if not cookie:
        return False
    try:
        return make_serializer(secret).loads(cookie) == SESSION_VALUE
    except BadSignature:
        return False


def hash_password(password: str) -> str:
    """Helper for the admin to generate a hash — invoked via __main__."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


if __name__ == "__main__":
    # Usage: python -m dashboard.auth
    import getpass
    pw = getpass.getpass("New admin password: ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2:
        raise SystemExit("passwords do not match")
    print(hash_password(pw))
