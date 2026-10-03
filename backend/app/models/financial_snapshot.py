"""Digital Twin storage: immutable snapshots, projection headers and month-by-month projected states."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (Date, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric,
                        SmallInteger, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, CreatedAtMixin, CurrencyCode, Hash64, Json, Money, Semver, UuidPk,
                             between, ck, is_hash, is_semver, json_object, non_negative, pg_enum)
from app.models.enums import ProjectionKind, SnapshotKind


class FinancialSnapshot(CreatedAtMixin, Base):
    """Immutable 'Current Financial State'. Typed summary columns for querying + full twin state as JSONB."""

    __tablename__ = "financial_snapshots"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    kind: Mapped[SnapshotKind] = mapped_column(pg_enum(SnapshotKind, "snapshot_kind"))
    as_of_date: Mapped[dt.date] = mapped_column(Date)
    monthly_income: Mapped[Money]
    monthly_expenses: Mapped[Money]
    total_savings: Mapped[Money]
    total_debt: Mapped[Money]
    monthly_debt_service: Mapped[Money]
    state: Mapped[Json]
    state_schema_version: Mapped[int] = mapped_column(SmallInteger)
    builder_version: Mapped[Semver]
    state_hash: Mapped[Hash64]

    projections: Mapped[list[FinancialProjection]] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_financial_snapshots_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", name="uq_financial_snapshots_id_user_id_currency"),
        UniqueConstraint("user_id", "as_of_date", "state_hash", name="uq_financial_snapshots_user_asof_hash"),
        non_negative("monthly_income"),
        non_negative("monthly_expenses"),
        non_negative("total_savings"),
        non_negative("total_debt"),
        non_negative("monthly_debt_service"),
        ck("state_required_keys",
           "jsonb_typeof(state) = 'object' AND state ?& ARRAY['income','expenses','savings','debts','goals','investments']"),
        ck("state_schema_version_positive", "state_schema_version > 0"),
        is_semver("builder_version"),
        is_hash("state_hash"),
        Index("ix_snapshots_user_asof", "user_id", text("as_of_date DESC")),
    )


class FinancialProjection(CreatedAtMixin, Base):
    """Header of a projection of a snapshot: 'baseline' (no decision) or 'scenario'."""

    __tablename__ = "financial_projections"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID]
    snapshot_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    kind: Mapped[ProjectionKind] = mapped_column(pg_enum(ProjectionKind, "projection_kind"))
    horizon_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[dt.date] = mapped_column(Date)
    engine_version: Mapped[Semver]
    assumptions_schema_version: Mapped[int] = mapped_column(SmallInteger)
    assumptions: Mapped[Json]
    inputs_hash: Mapped[Hash64]
    final_state: Mapped[Json]

    snapshot: Mapped[FinancialSnapshot] = relationship(viewonly=True)
    points: Mapped[list[FinancialProjectionPoint]] = relationship(
        viewonly=True, order_by="FinancialProjectionPoint.month_index")

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_financial_projections_id_user_id"),
        UniqueConstraint("id", "user_id", "snapshot_id", name="uq_financial_projections_id_user_id_snapshot"),
        UniqueConstraint("snapshot_id", "kind", "inputs_hash", "engine_version",
                         name="uq_financial_projections_snapshot_kind_hash_version"),
        ForeignKeyConstraint(["snapshot_id", "user_id", "currency"],
                             ["financial_snapshots.id", "financial_snapshots.user_id", "financial_snapshots.currency"],
                             name="fk_financial_projections_snapshot", ondelete="CASCADE"),
        between("horizon_months", "1", "720"),
        is_semver("engine_version"),
        ck("assumptions_schema_version_positive", "assumptions_schema_version > 0"),
        json_object("assumptions"),
        is_hash("inputs_hash"),
        json_object("final_state"),
    )


class FinancialProjectionPoint(Base):
    """One projected month. Signed columns are intentional (shortfalls must be visible)."""

    __tablename__ = "financial_projection_points"

    projection_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    month_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    user_id: Mapped[uuid.UUID]
    period_date: Mapped[dt.date] = mapped_column(Date)
    income: Mapped[Money]
    expenses: Mapped[Money]
    debt_service: Mapped[Money]
    decision_cash_flow: Mapped[Money]
    net_cash_flow: Mapped[Money]
    savings_balance: Mapped[Money]
    debt_balance: Mapped[Money]
    investment_value: Mapped[Money]
    net_worth: Mapped[Money]
    savings_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    extra: Mapped[Json] = mapped_column(server_default=text("'{}'::jsonb"))

    projection: Mapped[FinancialProjection] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(["projection_id", "user_id"],
                             ["financial_projections.id", "financial_projections.user_id"],
                             name="fk_financial_projection_points_projection", ondelete="CASCADE"),
        between("month_index", "0", "720"),
        non_negative("income"),
        non_negative("expenses"),
        non_negative("debt_service"),
        non_negative("debt_balance"),
        non_negative("investment_value"),
        json_object("extra"),
        Index("ix_proj_points_user", "user_id", "projection_id"),
    )
