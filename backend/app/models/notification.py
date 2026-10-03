from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Index, SmallInteger,
                        String, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Json, TimestampMixin, UuidPk, between, ck, json_object, non_negative, pg_enum
from app.models.enums import DeliveryChannel, DeliveryStatus, NotificationSeverity, NotificationType


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    type: Mapped[NotificationType] = mapped_column(pg_enum(NotificationType, "notification_type"))
    severity: Mapped[NotificationSeverity] = mapped_column(pg_enum(NotificationSeverity, "notification_severity"),
                                                           server_default=text("'info'"))
    title: Mapped[str] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(String(1000))
    payload: Mapped[Json] = mapped_column(server_default=text("'{}'::jsonb"))
    due_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    goal_id: Mapped[uuid.UUID | None]
    loan_id: Mapped[uuid.UUID | None]
    budget_id: Mapped[uuid.UUID | None]

    deliveries: Mapped[list[NotificationDelivery]] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_notifications_id_user_id"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_id_dedupe_key"),
        ForeignKeyConstraint(["goal_id", "user_id"], ["goals.id", "goals.user_id"],
                             name="fk_notifications_goal", ondelete="SET NULL (goal_id)"),
        ForeignKeyConstraint(["loan_id", "user_id"], ["loans.id", "loans.user_id"],
                             name="fk_notifications_loan", ondelete="SET NULL (loan_id)"),
        ForeignKeyConstraint(["budget_id", "user_id"], ["budgets.id", "budgets.user_id"],
                             name="fk_notifications_budget", ondelete="SET NULL (budget_id)"),
        json_object("payload"),
        Index("ix_notif_unread", "user_id", text("due_at DESC"),
              postgresql_where=text("read_at IS NULL AND dismissed_at IS NULL")),
        Index("ix_notif_user_created", "user_id", text("created_at DESC")),
        Index("ix_notif_goal", "goal_id", "user_id", postgresql_where=text("goal_id IS NOT NULL")),
        Index("ix_notif_loan", "loan_id", "user_id", postgresql_where=text("loan_id IS NOT NULL")),
        Index("ix_notif_budget", "budget_id", "user_id", postgresql_where=text("budget_id IS NOT NULL")),
    )


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[UuidPk]
    notification_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    channel: Mapped[DeliveryChannel] = mapped_column(pg_enum(DeliveryChannel, "delivery_channel"))
    status: Mapped[DeliveryStatus] = mapped_column(pg_enum(DeliveryStatus, "delivery_status"),
                                                   server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(255))
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    notification: Mapped[Notification] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("notification_id", "channel", name="uq_notification_deliveries_notification_channel"),
        ForeignKeyConstraint(["notification_id", "user_id"], ["notifications.id", "notifications.user_id"],
                             name="fk_notification_deliveries_notification", ondelete="CASCADE"),
        non_negative("attempts"),
        ck("sent_at_matches_status", "(status = 'sent') = (sent_at IS NOT NULL)"),
        Index("ix_deliveries_pending", "scheduled_at", postgresql_where=text("status = 'pending'")),
        Index("ix_deliveries_user", "user_id"),
    )


class NotificationPreference(TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    notification_type: Mapped[NotificationType] = mapped_column(
        pg_enum(NotificationType, "notification_type"), primary_key=True)
    in_app: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    email: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    lead_days: Mapped[int] = mapped_column(SmallInteger, server_default=text("3"))

    __table_args__ = (between("lead_days", "0", "30"),)
