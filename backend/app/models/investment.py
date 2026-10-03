from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (ARRAY, Boolean, Date, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        Numeric, SmallInteger, String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, CreatedAtMixin, CurrencyCode, Hash64, Json, Money, Rate, Semver,
                             TimestampMixin, UuidPk, between, ck, is_hash, is_semver, json_object,
                             non_negative, pg_enum)
from app.models.enums import CalcType, ContributionFrequency, InvestmentType, PeriodTiming


class InvestmentSimulation(TimestampMixin, Base):
    """Educational simulation definition with explicit typed assumptions (not a holding)."""

    __tablename__ = "investment_simulations"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    investment_type: Mapped[InvestmentType] = mapped_column(pg_enum(InvestmentType, "investment_type"))
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    initial_amount: Mapped[Money] = mapped_column(server_default=text("0"))
    periodic_contribution: Mapped[Money] = mapped_column(server_default=text("0"))
    contribution_frequency: Mapped[ContributionFrequency] = mapped_column(
        pg_enum(ContributionFrequency, "contribution_frequency"), server_default=text("'monthly'"))
    contribution_timing: Mapped[PeriodTiming] = mapped_column(
        pg_enum(PeriodTiming, "period_timing"), server_default=text("'end_of_period'"))
    annual_step_up_rate: Mapped[Rate] = mapped_column(server_default=text("0"))
    expected_annual_return: Mapped[Rate]
    alternate_returns: Mapped[list[Decimal]] = mapped_column(ARRAY(Numeric(9, 6)), server_default=text("'{}'"))
    inflation_rate: Mapped[Rate]
    compounding_periods_per_year: Mapped[int] = mapped_column(SmallInteger, server_default=text("12"))
    duration_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[dt.date] = mapped_column(Date)
    is_archived: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    runs: Mapped[list[InvestmentSimulationRun]] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_investment_simulations_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", name="uq_investment_simulations_id_user_id_currency"),
        ck("name_not_blank", "char_length(btrim(name)) > 0"),
        non_negative("initial_amount"),
        non_negative("periodic_contribution"),
        between("annual_step_up_rate", "0", "1"),
        between("expected_annual_return", "-1", "1"),
        ck("alternate_returns_max5", "cardinality(alternate_returns) <= 5"),
        between("inflation_rate", "0", "1"),
        ck("compounding_periods_allowed", "compounding_periods_per_year IN (1,2,4,12,365)"),
        between("duration_months", "1", "720"),
        ck("type_matches_amounts",
           "(investment_type = 'sip' AND periodic_contribution > 0) "
           "OR (investment_type = 'lump_sum' AND initial_amount > 0) "
           "OR (investment_type = 'sip_plus_lump_sum' AND periodic_contribution > 0 AND initial_amount > 0)"),
        Index("ix_invsim_user", "user_id", "is_archived", text("updated_at DESC")),
    )


class InvestmentSimulationRun(CreatedAtMixin, Base):
    """Immutable result of one simulation calculation."""

    __tablename__ = "investment_simulation_runs"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID]
    simulation_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    calc_type: Mapped[CalcType] = mapped_column(pg_enum(CalcType, "calc_type"))
    engine_version: Mapped[Semver]
    assumptions_schema_version: Mapped[int] = mapped_column(SmallInteger)
    assumptions: Mapped[Json]
    inputs_hash: Mapped[Hash64]
    result: Mapped[Json]

    simulation: Mapped[InvestmentSimulation] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_investment_simulation_runs_id_user_id"),
        UniqueConstraint("simulation_id", "inputs_hash", "engine_version", name="uq_invsim_runs_sim_hash_version"),
        ForeignKeyConstraint(["simulation_id", "user_id", "currency"],
                             ["investment_simulations.id", "investment_simulations.user_id",
                              "investment_simulations.currency"],
                             name="fk_investment_simulation_runs_simulation", ondelete="CASCADE"),
        ck("calc_type_allowed", "calc_type IN ('sip','lump_sum','compound_interest','future_value','inflation_adjusted')"),
        is_semver("engine_version"),
        ck("assumptions_schema_version_positive", "assumptions_schema_version > 0"),
        json_object("assumptions"),
        is_hash("inputs_hash"),
        json_object("result"),
        Index("ix_invsim_runs_sim", "simulation_id", text("created_at DESC")),
    )
