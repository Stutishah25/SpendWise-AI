"""AI Analysis Layer storage: sanitized context + validated output. No raw prompts, no PII."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, Index, Integer, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, Hash64, Json, UuidPk, ck, is_hash, json_object, non_negative, pg_enum
from app.models.enums import AiTask, AiValidationStatus
from app.models.investment import InvestmentSimulationRun
from app.models.scenario import ScenarioComparison, ScenarioRun


class AiAnalysis(CreatedAtMixin, Base):
    __tablename__ = "ai_analyses"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    task: Mapped[AiTask] = mapped_column(pg_enum(AiTask, "ai_task"))
    scenario_run_id: Mapped[uuid.UUID | None]
    comparison_id: Mapped[uuid.UUID | None]
    investment_simulation_run_id: Mapped[uuid.UUID | None]
    period_start: Mapped[dt.date | None] = mapped_column(Date)
    period_end: Mapped[dt.date | None] = mapped_column(Date)
    context_schema_version: Mapped[int] = mapped_column(SmallInteger)
    context: Mapped[Json]
    context_hash: Mapped[Hash64]
    prompt_version: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    user_question: Mapped[str | None] = mapped_column(String(500))
    output: Mapped[Json]
    validation_status: Mapped[AiValidationStatus] = mapped_column(pg_enum(AiValidationStatus, "ai_validation_status"))
    validation_details: Mapped[Json] = mapped_column(server_default=text("'{}'::jsonb"))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    scenario_run: Mapped[ScenarioRun | None] = relationship(viewonly=True)
    comparison: Mapped[ScenarioComparison | None] = relationship(viewonly=True)
    investment_simulation_run: Mapped[InvestmentSimulationRun | None] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(["scenario_run_id", "user_id"], ["scenario_runs.id", "scenario_runs.user_id"],
                             name="fk_ai_analyses_scenario_run", ondelete="CASCADE"),
        ForeignKeyConstraint(["comparison_id", "user_id"],
                             ["scenario_comparisons.id", "scenario_comparisons.user_id"],
                             name="fk_ai_analyses_comparison", ondelete="CASCADE"),
        ForeignKeyConstraint(["investment_simulation_run_id", "user_id"],
                             ["investment_simulation_runs.id", "investment_simulation_runs.user_id"],
                             name="fk_ai_analyses_investment_simulation_run", ondelete="CASCADE"),
        ck("context_schema_version_positive", "context_schema_version > 0"),
        json_object("context"),
        is_hash("context_hash"),
        json_object("output"),
        non_negative("input_tokens"),
        non_negative("output_tokens"),
        non_negative("latency_ms"),
        ck("single_source", "num_nonnulls(scenario_run_id, comparison_id, investment_simulation_run_id) <= 1"),
        ck("explain_scenario_needs_run", "task <> 'explain_scenario' OR scenario_run_id IS NOT NULL"),
        ck("compare_needs_comparison", "task <> 'compare_scenarios' OR comparison_id IS NOT NULL"),
        ck("explain_investment_needs_run", "task <> 'explain_investment' OR investment_simulation_run_id IS NOT NULL"),
        ck("period_order", "period_end IS NULL OR period_start IS NOT NULL AND period_end >= period_start"),
        Index("ix_ai_user_created", "user_id", text("created_at DESC")),
        Index("ix_ai_cache_lookup", "user_id", "task", "context_hash", "prompt_version", "model"),
        Index("ix_ai_run", "scenario_run_id", "user_id", postgresql_where=text("scenario_run_id IS NOT NULL")),
        Index("ix_ai_comparison", "comparison_id", "user_id", postgresql_where=text("comparison_id IS NOT NULL")),
        Index("ix_ai_invrun", "investment_simulation_run_id", "user_id",
              postgresql_where=text("investment_simulation_run_id IS NOT NULL")),
    )
