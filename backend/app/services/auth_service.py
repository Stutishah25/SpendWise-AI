"""Authentication service: registration, login, lockout, token rotation, and reuse detection."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (create_access_token, generate_raw_refresh_token,
                               hash_password, hash_refresh_token, verify_password)
from app.models import RefreshToken, User
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest

# Dummy Argon2id hash for constant-time comparison against nonexistent users
_DUMMY_ARGON2_HASH = "$argon2id$v=19$m=65536,t=2,p=1$c29tZXNhbHQxMjM0NTY3OA$9WJgZcZz6tY8R6z3pXpGzKj2p3R5z6tY8R6z3pXpGzI"


def register_user(db: Session, req: UserRegisterRequest) -> User:
    """Register a new user with Argon2id hashed password. Duplicate email raises 400."""
    email = req.email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    pwd_hash = hash_password(req.password)
    user = User(email=email, password_hash=pwd_hash, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_refresh_token_record(
    db: Session,
    user_id: uuid.UUID,
    family_id: uuid.UUID | None = None,
) -> tuple[str, RefreshToken]:
    """Generate a raw refresh token and store only its SHA-256 hex digest in refresh_tokens table."""
    raw_token = generate_raw_refresh_token()
    token_hash = hash_refresh_token(raw_token)
    fam_id = family_id or uuid.uuid4()
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db_token = RefreshToken(
        user_id=user_id,
        family_id=fam_id,
        token_hash=token_hash,
        issued_at=now,
        expires_at=expires_at,
    )
    db.add(db_token)
    db.flush()
    return raw_token, db_token


def authenticate_user(db: Session, req: UserLoginRequest) -> tuple[TokenResponse, User]:
    """Authenticate email & password. Handles failed login count, temporary lockout, and token issuance."""
    email = req.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    now = dt.datetime.now(dt.timezone.utc)

    if user is None:
        # Defeat timing analysis with a dummy check
        verify_password(_DUMMY_ARGON2_HASH, req.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check deleted_at
    if user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive or deleted",
        )

    # Check locked_until
    if user.locked_until is not None:
        if user.locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is temporarily locked due to multiple failed login attempts. Please try again later.",
            )
        else:
            # Lockout period expired; reset failed count and lock
            user.locked_until = None
            user.failed_login_count = 0

    # Verify password with Argon2id
    is_valid = verify_password(user.password_hash, req.password)
    if not is_valid:
        user.failed_login_count += 1
        if user.failed_login_count >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = now + dt.timedelta(minutes=settings.LOCKOUT_DURATION_MINUTES)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check is_active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive or deleted",
        )

    # Authentication successful: reset lockouts, update last_login_at
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now

    access_token = create_access_token(user.id)
    raw_refresh, _ = create_refresh_token_record(db, user.id)
    db.commit()
    db.refresh(user)

    tokens = TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return tokens, user


def rotate_refresh_token(db: Session, raw_token: str) -> TokenResponse:
    """Rotate a refresh token. Revokes the old token, issues a replacement in the same family.
    
    If an already revoked token is used, revokes all tokens in its family (reuse detection).
    """
    token_hash = hash_refresh_token(raw_token)
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    now = dt.datetime.now(dt.timezone.utc)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Check reuse
    if token.revoked_at is not None:
        # Reuse detected: revoke entire family immediately
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected. All sessions in this family have been revoked.",
        )

    # Check expiry
    if token.expires_at <= now:
        token.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    # Check user status
    user = db.get(User, token.user_id)
    if not user or not user.is_active or user.deleted_at is not None:
        token.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or deleted",
        )

    # Rotate: create new token in the same family, link replaced_by_id, revoke old token
    new_raw_token, new_db_token = create_refresh_token_record(
        db, user_id=token.user_id, family_id=token.family_id
    )
    token.revoked_at = now
    token.last_used_at = now
    token.replaced_by_id = new_db_token.id

    access_token = create_access_token(user.id)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_raw_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def revoke_refresh_token(db: Session, raw_token: str | None = None) -> None:
    """Revoke a refresh token on logout. Safe to repeat."""
    if not raw_token:
        return
    token_hash = hash_refresh_token(raw_token)
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if token and token.revoked_at is None:
        token.revoked_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
