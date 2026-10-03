from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (CHAR, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        Numeric, SmallInteger, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, CurrencyCode, Hash64, TimestampMixin, UuidPk, between, ck,
                             is_hash, non_negative, pg_enum)
from app.models.enums import AuthTokenPurpose


class User(TimestampMixin, Base):
    """Authentication identity. Root of every ownership path."""

    __tablename__ = "users"

    id: Mapped[UuidPk]
    email: Mapped[str] = mapped_column(CITEXT)
    password_hash: Mapped[str] = mapped_column(Text)  # argon2id
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    email_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True)
    preferences: Mapped[UserPreference | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        ck("email_length", "char_length(email) BETWEEN 3 AND 254"),
        non_negative("failed_login_count"),
    )


class UserProfile(TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(120))
    display_name: Mapped[str | None] = mapped_column(String(60))
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    timezone: Mapped[str] = mapped_column(String(64), server_default=text("'Asia/Kolkata'"))

    user: Mapped[User] = relationship(back_populates="profile")

    __table_args__ = (ck("country_code_format", "country_code ~ '^[A-Z]{2}$'"),)


class UserPreference(TimestampMixin, Base):
    """Per-user defaults. They are COPIED into scenarios at creation time (never read implicitly)."""

    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    base_currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    locale: Mapped[str] = mapped_column(String(16))
    default_inflation_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    default_expected_return: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    default_horizon_months: Mapped[int] = mapped_column(Integer)
    budget_alert_threshold: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    user: Mapped[User] = relationship(back_populates="preferences")

    __table_args__ = (
        between("default_inflation_rate", "0", "1"),
        between("default_expected_return", "-1", "1"),
        between("default_horizon_months", "1", "720"),
        ck("budget_alert_threshold_range", "budget_alert_threshold > 0 AND budget_alert_threshold <= 1"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID]
    token_hash: Mapped[Hash64]
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None]

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        UniqueConstraint("id", "user_id", name="uq_refresh_tokens_id_user_id"),
        ForeignKeyConstraint(["replaced_by_id", "user_id"], ["refresh_tokens.id", "refresh_tokens.user_id"],
                             name="fk_refresh_tokens_replaced_by", ondelete="SET NULL (replaced_by_id)"),
        is_hash("token_hash"),
        ck("expiry_after_issue", "expires_at > issued_at"),
        Index("ix_refresh_tokens_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_expires", "expires_at"),
        Index("ix_refresh_tokens_replaced", "replaced_by_id", "user_id",
              postgresql_where=text("replaced_by_id IS NOT NULL")),
    )


class AuthToken(Base):
    """Single-use tokens for email verification and password reset (hash only)."""

    __tablename__ = "auth_tokens"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[AuthTokenPurpose] = mapped_column(pg_enum(AuthTokenPurpose, "auth_token_purpose"))
    token_hash: Mapped[Hash64]
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
        is_hash("token_hash"),
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
    )
