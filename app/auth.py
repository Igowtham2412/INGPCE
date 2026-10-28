"""Password hashing and signed, revocable browser session helpers."""
import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from typing import Any

import bcrypt

from . import config, db


def hash_password(password: str) -> str:
    password_digest = hashlib.sha256(password.encode("utf-8")).digest()
    encoded = bcrypt.hashpw(password_digest, bcrypt.gensalt(rounds=12))
    return "bcrypt_sha256$" + encoded.decode("ascii")


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, bcrypt_hash = encoded.split("$", 1)
        if algorithm != "bcrypt_sha256":
            return False
        encoded_hash = bcrypt_hash.encode("ascii")
        if not encoded_hash.startswith((b"$2a$", b"$2b$", b"$2y$")):
            return False
        password_digest = hashlib.sha256(password.encode("utf-8")).digest()
        return bcrypt.checkpw(password_digest, encoded_hash)
    except (ValueError, TypeError, UnicodeError):
        return False


def create_session_token(session_id: str, expires_at: int, secret: str) -> str:
    payload = f"{session_id}.{expires_at}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload}.{encoded}"


def parse_session_token(token: str, secret: str, now: int | None = None) -> tuple[str, int] | None:
    try:
        session_id, expires_text, signature = token.split(".", 2)
        expires_at = int(expires_text)
        if not session_id or expires_at <= (int(time.time()) if now is None else now):
            return None
        expected = create_session_token(session_id, expires_at, secret).rsplit(".", 1)[1]
        if not hmac.compare_digest(signature, expected):
            return None
        return session_id, expires_at
    except (ValueError, TypeError, UnicodeError):
        return None


async def authenticate_token(token: str) -> dict[str, Any] | None:
    if not config.AUTH_ENABLED:
        return None
    parsed = parse_session_token(token, config.AUTH_SESSION_SECRET)
    if parsed is None:
        return None
    session_id, expires_at = parsed
    return await db.get_auth_session(session_id, expires_at)


async def create_session(user: dict[str, Any]):
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + config.AUTH_SESSION_HOURS * 3600
    await db.create_auth_session(
        session_id, int(user["id"]), csrf_token,
        datetime.fromtimestamp(expires_at, tz=UTC),
    )
    token = create_session_token(session_id, expires_at, config.AUTH_SESSION_SECRET)
    return token, csrf_token, expires_at


def invite_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
