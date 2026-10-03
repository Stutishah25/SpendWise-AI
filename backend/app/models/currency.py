from __future__ import annotations

from sqlalchemy import CHAR, Boolean, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ck


class Currency(Base):
    """Reference data. `symbol` is presentation metadata only; calculations never read it."""

    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    symbol: Mapped[str] = mapped_column(String(8))
    minor_units: Mapped[int] = mapped_column(SmallInteger)
    default_locale: Mapped[str] = mapped_column(String(16))
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        ck("code_format", "code ~ '^[A-Z]{3}$'"),
        ck("minor_units_range", "minor_units BETWEEN 0 AND 4"),
    )
