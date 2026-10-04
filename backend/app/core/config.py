"""Configuration settings for SpendWise AI backend."""
from __future__ import annotations

import os


class Settings:
    JWT_SECRET_KEY: str = os.environ.get(
        "JWT_SECRET_KEY", "spendwise-dev-jwt-secret-key-32-chars-minimum!"
    )
    JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    MAX_FAILED_LOGIN_ATTEMPTS: int = int(os.environ.get("MAX_FAILED_LOGIN_ATTEMPTS", "5"))
    LOCKOUT_DURATION_MINUTES: int = int(os.environ.get("LOCKOUT_DURATION_MINUTES", "15"))


settings = Settings()
