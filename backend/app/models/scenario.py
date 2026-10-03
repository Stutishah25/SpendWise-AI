"""Scenario Simulation Engine storage: definitions, immutable runs, and 2-3 way comparisons."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, SmallInteger,
                        String, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, CreatedAtMixin, CurrencyCode, Hash64, Json, Semver, TimestampMixin,
                             UuidPk, between, ck, is_hash, is_semver, json_object, pg_enum)
from app.models.enums import CalcType, ScenarioStatus, ScenarioType
from app.models.financial_snapshot import FinancialProjection, FinancialSnapshot


class Scenario(TimestampMixin, Base):
    """Editable definition. Results are never stored here; each execution is an immutable ScenarioRun."""

    __tablename__ = "scenarios"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(1000))
    scenario_type: Mapped[ScenarioType] = mapped_column(pg_enum(ScenarioType, "scenario_type"))
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    assumptions_schema_version: Mapped[int] = mapped_column(SmallInteger)
    assumptions: Mapped[Json]
    status: Mapped[ScenarioStatus] = mapped_column(pg_enum(ScenarioStatus, "scenario_status"),
                                                   server_default=text("'draft'"))

    runs: Mapped[list[ScenarioRun]] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_scenarios_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", "scenario_type", name="uq_scenarios_id_user_id_currency_type"),
        ck("name_not_blank", "char_length(btrim(name)) > 0"),
        ck("assumptions_schema_version_positive", "assumptions_schema_version > 0"),
        ck("assumptions_required_keys",
           "jsonb_typeof(assumptions) = 'object' AND assumptions ?& ARRAY['horizon_months','starting_balance',"
           "'monthly_income','monthly_expenses','inflation_rate','expected_annual_return']"),
        Index("ix_scenarios_user_status", "user_id", "status", text("updated_at DESC")),
        Index("ix_scenarios_user_type", "user_id", "scenario_type"),
    )


class ScenarioRun(CreatedAtMixin, Base):
    """Immutable, reproducible result: frozen assumptions + snapshot + engine version + hash + result."""

    __tablename__ = "scenario_runs"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID]
    scenario_id: Mapped[uuid.UUID]
    snapshot_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    scenario_type: Mapped[ScenarioType] = mapped_column(pg_enum(ScenarioType, "scenario_type"))
    calc_type: Mapped[CalcType] = mapped_column(pg_enum(CalcType, "calc_type"),
                                                server_default=text("'scenario_simulation'"))
    horizon_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[dt.date] = mapped_column(Date)
    engine_version: Mapped[Semver]
    assumptions_schema_version: Mapped[int] = mapped_column(SmallInteger)
    assumptions: Mapped[Json]
    inputs_hash: Mapped[Hash64]
    baseline_projection_id: Mapped[uuid.UUID]
    scenario_projection_id: Mapped[uuid.UUID]
    result: Mapped[Json]

    scenario: Mapped[Scenario] = relationship(viewonly=True)
    snapshot: Mapped[FinancialSnapshot] = relationship(viewonly=True)
    baseline_projection: Mapped[FinancialProjection] = relationship(
        viewonly=True,
        primaryjoin="and_(FinancialProjection.id == foreign(ScenarioRun.baseline_projection_id), "
                    "FinancialProjection.user_id == foreign(ScenarioRun.user_id))")
    scenario_projection: Mapped[FinancialProjection] = relationship(
        viewonly=True,
        primaryjoin="and_(FinancialProjection.id == foreign(ScenarioRun.scenario_projection_id), "
                    "FinancialProjection.user_id == foreign(ScenarioRun.user_id))")

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_scenario_runs_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", "snapshot_id", "horizon_months",
                         name="uq_scenario_runs_comparability_key"),
        UniqueConstraint("scenario_id", "inputs_hash", "engine_version", name="uq_scenario_runs_scenario_hash_version"),
        ForeignKeyConstraint(["scenario_id", "user_id", "currency", "scenario_type"],
                             ["scenarios.id", "scenarios.user_id", "scenarios.currency", "scenarios.scenario_type"],
                             name="fk_scenario_runs_scenario", ondelete="CASCADE"),
        ForeignKeyConstraint(["snapshot_id", "user_id", "currency"],
                             ["financial_snapshots.id", "financial_snapshots.user_id", "financial_snapshots.currency"],
                             name="fk_scenario_runs_snapshot", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["baseline_projection_id", "user_id", "snapshot_id"],
                             ["financial_projections.id", "financial_projections.user_id",
                              "financial_projections.snapshot_id"], name="fk_scenario_runs_baseline_projection", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["scenario_projection_id", "user_id", "snapshot_id"],
                             ["financial_projections.id", "financial_projections.user_id",
                              "financial_projections.snapshot_id"], name="fk_scenario_runs_scenario_projection", deferrable=True, initially="DEFERRED"),
        ck("calc_type_scenario_simulation", "calc_type = 'scenario_simulation'"),
        between("horizon_months", "1", "720"),
        is_semver("engine_version"),
        ck("assumptions_schema_version_positive", "assumptions_schema_version > 0"),
        json_object("assumptions"),
        ck("horizon_matches_assumptions", "assumptions ->> 'horizon_months' = horizon_months::text"),
        is_hash("inputs_hash"),
        json_object("result"),
        ck("projections_distinct", "baseline_projection_id <> scenario_projection_id"),
        Index("ix_runs_user_created", "user_id", text("created_at DESC")),
        Index("ix_runs_scenario", "scenario_id", text("created_at DESC")),
        Index("ix_runs_snapshot", "snapshot_id", "user_id"),
        Index("ix_runs_proj_base", "baseline_projection_id"),
        Index("ix_runs_proj_scen", "scenario_projection_id"),
    )


class ScenarioComparison(TimestampMixin, Base):
    """Result of comparing 2-3 runs. Only `name` (and updated_at) may change after creation."""

    __tablename__ = "scenario_comparisons"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    snapshot_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    horizon_months: Mapped[int] = mapped_column(Integer)
    calc_type: Mapped[CalcType] = mapped_column(pg_enum(CalcType, "calc_type"),
                                                server_default=text("'scenario_comparison'"))
    engine_version: Mapped[Semver]
    inputs_hash: Mapped[Hash64]
    result: Mapped[Json]

    items: Mapped[list[ScenarioComparisonItem]] = relationship(
        viewonly=True, order_by="ScenarioComparisonItem.position")

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_scenario_comparisons_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", "snapshot_id", "horizon_months",
                         name="uq_scenario_comparisons_comparability_key"),
        UniqueConstraint("user_id", "inputs_hash", "engine_version", name="uq_scenario_comparisons_user_hash_version"),
        ForeignKeyConstraint(["snapshot_id", "user_id", "currency"],
                             ["financial_snapshots.id", "financial_snapshots.user_id", "financial_snapshots.currency"],
                             name="fk_scenario_comparisons_snapshot", deferrable=True, initially="DEFERRED"),
        ck("name_not_blank", "char_length(btrim(name)) > 0"),
        between("horizon_months", "1", "720"),
        ck("calc_type_scenario_comparison", "calc_type = 'scenario_comparison'"),
        is_semver("engine_version"),
        is_hash("inputs_hash"),
        json_object("result"),
        Index("ix_comparisons_user_created", "user_id", text("created_at DESC")),
        Index("ix_comparisons_snapshot", "snapshot_id", "user_id"),
    )


class ScenarioComparisonItem(Base):
    """Positions 1-3. The composite FKs force every item to share owner, currency, snapshot and horizon
    with BOTH the comparison and the run, so incomparable runs cannot be combined."""

    __tablename__ = "scenario_comparison_items"

    comparison_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    user_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    snapshot_id: Mapped[uuid.UUID]
    horizon_months: Mapped[int] = mapped_column(Integer)
    scenario_run_id: Mapped[uuid.UUID]
    label: Mapped[str] = mapped_column(String(60))

    run: Mapped[ScenarioRun] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("comparison_id", "scenario_run_id", name="uq_scenario_comparison_items_comparison_run"),
        ForeignKeyConstraint(
            ["comparison_id", "user_id", "currency", "snapshot_id", "horizon_months"],
            ["scenario_comparisons.id", "scenario_comparisons.user_id", "scenario_comparisons.currency",
             "scenario_comparisons.snapshot_id", "scenario_comparisons.horizon_months"],
            name="fk_scenario_comparison_items_comparison", ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["scenario_run_id", "user_id", "currency", "snapshot_id", "horizon_months"],
            ["scenario_runs.id", "scenario_runs.user_id", "scenario_runs.currency",
             "scenario_runs.snapshot_id", "scenario_runs.horizon_months"],
            name="fk_scenario_comparison_items_run", deferrable=True, initially="DEFERRED"),
        between("position", "1", "3"),
        Index("ix_comp_items_run", "scenario_run_id"),
    )
