import hashlib
import hmac
import os

_SALT = os.environ.get("OPEX_SALT", "opex-demo-salt").encode()


def _hash(password):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), _SALT, 120_000).hex()


USERS = {
    "analyst": {"hash": _hash(os.environ.get("OPEX_ANALYST_PASSWORD", "analyst123")), "role": "viewer", "name": "Demo Analyst"},
    "admin": {"hash": _hash(os.environ.get("OPEX_ADMIN_PASSWORD", "admin123")), "role": "admin", "name": "Demo Admin"},
}

PERMISSIONS = {
    "viewer": {"download": False, "show_sql": False},
    "admin": {"download": True, "show_sql": True},
}


def authenticate(username, password):
    user = USERS.get((username or "").strip().lower())
    candidate = _hash(password or "")
    if user and hmac.compare_digest(user["hash"], candidate):
        return {"user": username.strip().lower(), "role": user["role"], "name": user["name"]}
    return None


def can(session, permission):
    role = (session or {}).get("role")
    return bool(role and PERMISSIONS.get(role, {}).get(permission))
