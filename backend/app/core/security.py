"""Cryptographic security utilities: Argon2id hashing, JWT access tokens, refresh token hashing."""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
import jwt

from app.core.config import settings

_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
)


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _hasher.hash(password)


def verify_password(password_hash: str, plain_password: str) -> bool:
    """Verify a password against an Argon2id hash.
    
    Returns False immediately if the hash is a non-verifiable placeholder (e.g. starts with '!').
    """
    if not password_hash or password_hash.startswith("!"):
        return False
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(
    subject: str | uuid.UUID,
    expires_delta: dt.timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT access token."""
    now = dt.datetime.now(dt.timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + dt.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_raw_refresh_token() -> str:
    """Generate a cryptographically secure URL-safe random string for refresh token."""
    return secrets.token_urlsafe(64)


def hash_refresh_token(raw_token: str) -> str:
    """Hash a raw refresh token using SHA-256 hex digest (64 lowercase hex characters)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
