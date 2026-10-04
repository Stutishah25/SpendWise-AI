"""Authentication endpoints: register, login, refresh, logout, me."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.dependencies import get_db
from app.models import User
from app.schemas.auth import (LogoutRequest, MessageResponse, RefreshTokenRequest,
                              TokenResponse, UserLoginRequest, UserRegisterRequest,
                              UserResponse)
from app.services.auth_service import (authenticate_user, register_user,
                                       revoke_refresh_token, rotate_refresh_token)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    req: UserRegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Register a new user account with validated email and password."""
    return register_user(db, req)


@router.post("/login", response_model=TokenResponse)
def login(
    req: UserLoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Authenticate user credentials and issue access + refresh tokens."""
    tokens, _ = authenticate_user(db, req)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    req: RefreshTokenRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Rotate a refresh token, issuing a new access token and a replacement refresh token."""
    return rotate_refresh_token(db, req.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(
    db: Annotated[Session, Depends(get_db)],
    req: LogoutRequest | None = None,
) -> MessageResponse:
    """Revoke the provided refresh token. Safe to repeat."""
    raw_token = req.refresh_token if req else None
    revoke_refresh_token(db, raw_token)
    return MessageResponse(message="Successfully logged out")


@router.get("/me", response_model=UserResponse)
def read_current_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get the authenticated user's safe profile information."""
    return current_user
