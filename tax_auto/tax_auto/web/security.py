"""Password hashing + verification + policy.

Uses `bcrypt` directly (passlib 1.7.x is incompatible with bcrypt 5.x).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

import bcrypt

# bcrypt has a 72-byte input limit; pre-hash with SHA-256 to support any length
# and to avoid leaking the original via length-side-channel.
_PEPPER = b"fapiao-pw-"  # fixed prefix to differentiate from raw bcrypt hashes


def _prepare(password: str) -> bytes:
    """Pre-hash to 32 bytes so we always fit bcrypt's 72-byte limit."""
    return _PEPPER + hashlib.sha256(password.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    """Return bcrypt hash (12 rounds, ASCII-safe string)."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(_prepare(password), salt).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time verify."""
    try:
        return bcrypt.checkpw(_prepare(password), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ─── Password policy ───────────────────────────────────────────────

_MIN_LEN = 10


def check_password_policy(password: str, username: str | None = None) -> str | None:
    """Return None if OK, else a human-readable Chinese reason string."""
    if len(password) < _MIN_LEN:
        return f"密码长度不能少于 {_MIN_LEN} 位"
    if not re.search(r"[A-Za-z]", password):
        return "密码必须包含字母"
    if not re.search(r"[0-9]", password):
        return "密码必须包含数字"
    if username and password.lower() == username.lower():
        return "密码不能等于用户名"
    return None


# ─── CSRF token ─────────────────────────────────────────────────────


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
