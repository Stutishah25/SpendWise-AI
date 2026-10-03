from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Json, ck, pg_enum
from app.models.enums import AuditOutcome


class AuditLog(Base):
    """Append-only by privilege (runtime role: INSERT + SELECT only). user_id is anonymised on account deletion."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[uuid.UUID | None]
    outcome: Mapped[AuditOutcome] = mapped_column(pg_enum(AuditOutcome, "audit_outcome"))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET)
    metadata_: Mapped[Json] = mapped_column("metadata", server_default=text("'{}'::jsonb"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ck("metadata_is_object", "jsonb_typeof(metadata) = 'object'"),
        Index("ix_audit_user_created", "user_id", text("created_at DESC")),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_action", "action", text("created_at DESC")),
    )
