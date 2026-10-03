from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric,
                        String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, Json, CreatedAtMixin, CurrencyCode, Hash64, Money, Rate, Semver,
                             TimestampMixin, UuidPk, ck, is_hash, is_semver, json_object,
                             non_negative, pg_enum, positive)
from app.models.enums import CalcType, GoalEntryKind, GoalStatus, GoalType


class Goal(TimestampMixin, Base):
    """User-entered goal. Progress %, required contribution and ETA are NOT stored here:
    they are engine outputs (see GoalProgressSnapshot)."""

    __tablename__ = "goals"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    goal_type: Mapped[GoalType] = mapped_column(pg_enum(GoalType, "goal_type"))
    target_amount: Mapped[Money]
    starting_amount: Mapped[Money] = mapped_column(server_default=text("0"))
    planned_monthly_contribution: Mapped[Money] = mapped_column(server_default=text("0"))
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    target_date: Mapped[dt.date | None] = mapped_column(Date)
    status: Mapped[GoalStatus] = mapped_column(pg_enum(GoalStatus, "goal_status"), server_default=text("'active'"))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    contributions: Mapped[list[GoalContribution]] = relationship(viewonly=True)
    progress_snapshots: Mapped[list[GoalProgressSnapshot]] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_goals_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", name="uq_goals_id_user_id_currency"),
        ck("name_not_blank", "char_length(btrim(name)) > 0"),
        positive("target_amount"),
        non_negative("starting_amount"),
        non_negative("planned_monthly_contribution"),
        ck("completed_at_matches_status", "(status = 'completed') = (completed_at IS NOT NULL)"),
        Index("ix_goals_user_status", "user_id", "status"),
        Index("ix_goals_active_target_date", "user_id", "target_date", postgresql_where=text("status = 'active'")),
    )


class GoalContribution(CreatedAtMixin, Base):
    """Ledger of deposits/withdrawals. Current amount = starting_amount + signed sum (view v_goal_current_amounts)."""

    __tablename__ = "goal_contributions"

    id: Mapped[UuidPk]
    goal_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    kind: Mapped[GoalEntryKind] = mapped_column(pg_enum(GoalEntryKind, "goal_entry_kind"),
                                                server_default=text("'contribution'"))
    amount: Mapped[Money]
    contribution_date: Mapped[dt.date] = mapped_column(Date)

    goal: Mapped[Goal] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(["goal_id", "user_id", "currency"], ["goals.id", "goals.user_id", "goals.currency"],
                             name="fk_goal_contributions_goal", ondelete="CASCADE"),
        positive("amount"),
        Index("ix_goal_contrib_goal_date", "goal_id", text("contribution_date DESC")),
        Index("ix_goal_contrib_user_date", "user_id", text("contribution_date DESC")),
    )


class GoalProgressSnapshot(CreatedAtMixin, Base):
    """Immutable periodic output of the goal-contribution calculation (inputs + version preserved)."""

    __tablename__ = "goal_progress_snapshots"

    id: Mapped[UuidPk]
    goal_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    snapshot_date: Mapped[dt.date] = mapped_column(Date)
    current_amount: Mapped[Money]
    target_amount: Mapped[Money]
    progress_ratio: Mapped[Rate]
    required_monthly_contribution: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    estimated_completion_date: Mapped[dt.date | None] = mapped_column(Date)
    calc_type: Mapped[CalcType] = mapped_column(pg_enum(CalcType, "calc_type"),
                                                server_default=text("'goal_contribution'"))
    engine_version: Mapped[Semver]
    assumptions: Mapped[Json]
    inputs_hash: Mapped[Hash64]

    goal: Mapped[Goal] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(["goal_id", "user_id", "currency"], ["goals.id", "goals.user_id", "goals.currency"],
                             name="fk_goal_progress_snapshots_goal", ondelete="CASCADE"),
        UniqueConstraint("goal_id", "snapshot_date", "engine_version", name="uq_goal_snap_goal_date_version"),
        non_negative("current_amount"),
        positive("target_amount"),
        non_negative("progress_ratio"),
        non_negative("required_monthly_contribution"),
        ck("calc_type_goal_contribution", "calc_type = 'goal_contribution'"),
        is_semver("engine_version"),
        json_object("assumptions"),
        is_hash("inputs_hash"),
        Index("ix_goal_snap_user_date", "user_id", text("snapshot_date DESC")),
    )
