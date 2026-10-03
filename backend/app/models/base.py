"""Declarative base, naming convention and shared column types.

Money      NUMERIC(18,4)  exact decimal; never float. 14 integer digits, 4 fractional digits.
Rate       NUMERIC(9,6)   rates/ratios stored as fractions (0.095 == 9.5%).
All timestamps are TIMESTAMPTZ (UTC); business dates are DATE.
"""
from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal
from typing import Annotated, Any

from sqlalchemy import CHAR, CheckConstraint, DateTime, MetaData, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    # Unique constraints are always given explicit names (composite keys are long).
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        dict[str, Any]: JSONB,
        uuid.UUID: PGUUID(as_uuid=True),
        dt.datetime: DateTime(timezone=True),
    }


# ----- reusable annotated column types ---------------------------------------------------
Money = Annotated[Decimal, mapped_column(Numeric(18, 4))]
Rate = Annotated[Decimal, mapped_column(Numeric(9, 6))]
UuidPk = Annotated[
    uuid.UUID,
    mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
]
CurrencyCode = Annotated[str, mapped_column(CHAR(3))]
Hash64 = Annotated[str, mapped_column(CHAR(64))]  # hex SHA-256 (CHAR(64) in the DDL)
Semver = Annotated[str, mapped_column(String(32))]
Json = Annotated[dict[str, Any], mapped_column(JSONB)]  # always a JSON *object* (CHECKed in the DDL)


def pg_enum(cls: type[enum.Enum], name: str) -> PGEnum:
    """PostgreSQL ENUM bound to a Python enum; the type itself is created by the migration."""
    return PGEnum(cls, name=name, values_callable=lambda e: [m.value for m in e], create_type=False)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
        server_onupdate=func.now(),  # maintained by the set_updated_at() trigger
    )


class CreatedAtMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ----- constraint helpers (every CHECK is named so Alembic can track it) ------------------
def ck(name: str, sql: str) -> CheckConstraint:
    return CheckConstraint(sql, name=name)


def positive(col: str) -> CheckConstraint:
    return ck(f"{col}_positive", f"{col} > 0")


def non_negative(col: str) -> CheckConstraint:
    return ck(f"{col}_non_negative", f"{col} >= 0")


def between(col: str, lo: str, hi: str, name: str | None = None) -> CheckConstraint:
    return ck(name or f"{col}_range", f"{col} BETWEEN {lo} AND {hi}")


def is_hash(col: str) -> CheckConstraint:
    return ck(f"{col}_hex64", f"{col} ~ '^[0-9a-f]{{64}}$'")


def is_semver(col: str) -> CheckConstraint:
    return ck(f"{col}_semver", rf"{col} ~ '^[0-9]+\.[0-9]+\.[0-9]+$'")


def json_object(col: str) -> CheckConstraint:
    return ck(f"{col}_is_object", f"jsonb_typeof({col}) = 'object'")
