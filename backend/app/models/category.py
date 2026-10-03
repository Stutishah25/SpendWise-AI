from __future__ import annotations

import uuid

from sqlalchemy import CHAR, Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPk, ck, pg_enum
from app.models.enums import TxnKind


class Category(TimestampMixin, Base):
    """Per-user category (defaults are copied per user at signup, so every row has an owner)."""

    __tablename__ = "categories"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[TxnKind] = mapped_column(pg_enum(TxnKind, "txn_kind"))
    name: Mapped[str] = mapped_column(String(60))
    system_key: Mapped[str | None] = mapped_column(String(40))
    color: Mapped[str | None] = mapped_column(CHAR(7))
    icon: Mapped[str | None] = mapped_column(String(40))
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        UniqueConstraint("id", "user_id", "kind", name="uq_categories_id_user_id_kind"),
        UniqueConstraint("user_id", "system_key", name="uq_categories_user_id_system_key"),
        ck("name_not_blank", "char_length(btrim(name)) > 0"),
        ck("color_hex", "color ~ '^#[0-9A-Fa-f]{6}$'"),
        Index("ux_categories_user_kind_name", "user_id", "kind", text("lower(name)"), unique=True),
    )
