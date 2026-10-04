"""FastAPI dependencies for authentication, user resolution, and RLS context."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.dependencies import get_db, set_session_rls_user
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Validate access token, load active user, and set app.current_user_id for RLS."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    expired_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token has expired",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise expired_exception
    except jwt.PyJWTError:
        raise credentials_exception

    token_type = payload.get("type")
    if token_type != "access":
        raise credentials_exception

    user_id_raw = payload.get("sub")
    if not user_id_raw:
        raise credentials_exception

    try:
        user_id = uuid.UUID(str(user_id_raw))
    except (ValueError, TypeError):
        raise credentials_exception

    user = db.get(User, user_id)
    if not user:
        raise credentials_exception

    if not user.is_active or user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or deleted",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # CRITICAL: Set app.current_user_id in PostgreSQL session/transaction for Row Level Security
    set_session_rls_user(db, user.id)

    return user


def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Convenience alias for endpoints requiring an authenticated active user."""
    return current_user
