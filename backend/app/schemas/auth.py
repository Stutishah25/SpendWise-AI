"""Pydantic schemas for authentication and user accounts."""
from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class UserRegisterRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if len(v) < 3 or len(v) > 254:
            raise ValueError("Email must be between 3 and 254 characters")
        if not EMAIL_REGEX.match(v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if len(v) > 128:
            raise ValueError("Password must not exceed 128 characters")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter")
        if not (any(c.isdigit() for c in v) or any(c in "!@#$%^&*()-_=+[]{}|;:',.<>?/`~" for c in v)):
            raise ValueError("Password must contain at least one digit or special character")
        return v


class UserLoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def clean_email(cls, v: str) -> str:
        return v.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: str | None = None
    display_name: str | None = None
    country_code: str | None = None
    timezone: str | None = None


class UserPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    base_currency: str
    locale: str
    default_inflation_rate: Decimal
    default_expected_return: Decimal
    default_horizon_months: int
    budget_alert_threshold: Decimal
    onboarding_completed: bool


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    is_active: bool
    email_verified_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime
    profile: UserProfileResponse | None = None
    preferences: UserPreferenceResponse | None = None


class MessageResponse(BaseModel):
    message: str
