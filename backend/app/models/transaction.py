from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric,
                        SmallInteger, String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, CurrencyCode, Money, TimestampMixin, UuidPk, between, ck,
                             pg_enum, positive)
from app.models.category import Category
from app.models.enums import RecurrenceFrequency, TxnKind, TxnSource

_CAT_FK_COLS = ["categories.id", "categories.user_id", "categories.kind"]


class RecurringRule(TimestampMixin, Base):
    __tablename__ = "recurring_rules"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID]
    kind: Mapped[TxnKind] = mapped_column(pg_enum(TxnKind, "txn_kind"))
    amount: Mapped[Money]
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    description: Mapped[str | None] = mapped_column(String(255))
    frequency: Mapped[RecurrenceFrequency] = mapped_column(pg_enum(RecurrenceFrequency, "recurrence_frequency"))
    interval_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    day_of_month: Mapped[int | None] = mapped_column(SmallInteger)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    next_run_date: Mapped[dt.date | None] = mapped_column(Date)
    last_run_date: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    category: Mapped[Category] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_recurring_rules_id_user_id"),
        ForeignKeyConstraint(["category_id", "user_id", "kind"], _CAT_FK_COLS, name="fk_recurring_rules_category", deferrable=True, initially="DEFERRED"),
        positive("amount"),
        between("interval_count", "1", "60"),
        between("day_of_month", "1", "31"),
        ck("end_after_start", "end_date IS NULL OR end_date >= start_date"),
        ck("active_has_next_run", "NOT is_active OR next_run_date IS NOT NULL"),
        Index("ix_recurring_rules_due", "next_run_date", postgresql_where=text("is_active")),
        Index("ix_recurring_rules_user", "user_id"),
        Index("ix_recurring_rules_category_fk", "category_id", "user_id"),
    )


class Transaction(TimestampMixin, Base):
    """Income and expenses (discriminated by `kind`). The kind must match the category's kind."""

    __tablename__ = "transactions"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID]
    kind: Mapped[TxnKind] = mapped_column(pg_enum(TxnKind, "txn_kind"))
    amount: Mapped[Money]
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    transaction_date: Mapped[dt.date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[TxnSource] = mapped_column(pg_enum(TxnSource, "txn_source"), server_default=text("'manual'"))
    recurring_rule_id: Mapped[uuid.UUID | None]
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    category: Mapped[Category] = relationship(viewonly=True)
    recurring_rule: Mapped[RecurringRule | None] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_transactions_id_user_id"),
        ForeignKeyConstraint(["category_id", "user_id", "kind"], _CAT_FK_COLS, name="fk_transactions_category", deferrable=True, initially="DEFERRED"),
        ForeignKeyConstraint(["recurring_rule_id", "user_id"], ["recurring_rules.id", "recurring_rules.user_id"],
                             name="fk_transactions_recurring_rule", ondelete="SET NULL (recurring_rule_id)"),
        positive("amount"),
        ck("transaction_date_range", "transaction_date BETWEEN DATE '1990-01-01' AND DATE '2100-12-31'"),
        Index("ix_txn_user_date", "user_id", text("transaction_date DESC"), postgresql_where=text("deleted_at IS NULL")),
        Index("ix_txn_user_kind_date", "user_id", "kind", text("transaction_date DESC"),
              postgresql_where=text("deleted_at IS NULL")),
        Index("ix_txn_user_cat_date", "user_id", "category_id", text("transaction_date DESC"),
              postgresql_where=text("deleted_at IS NULL")),
        Index("ix_txn_category_fk", "category_id", "user_id"),
        Index("ix_txn_desc_trgm", "user_id", "description", postgresql_using="gin",
              postgresql_ops={"description": "gin_trgm_ops"}, postgresql_where=text("deleted_at IS NULL")),
        Index("ux_txn_recurring_instance", "recurring_rule_id", "transaction_date", unique=True,
              postgresql_where=text("recurring_rule_id IS NOT NULL")),
    )


class Budget(TimestampMixin, Base):
    __tablename__ = "budgets"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[uuid.UUID]
    category_kind: Mapped[TxnKind] = mapped_column(pg_enum(TxnKind, "txn_kind"), server_default=text("'expense'"))
    month: Mapped[dt.date] = mapped_column(Date)
    limit_amount: Mapped[Money]
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    alert_threshold: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    category: Mapped[Category] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_budgets_id_user_id"),
        UniqueConstraint("user_id", "category_id", "month", name="uq_budgets_user_id_category_id_month"),
        ForeignKeyConstraint(["category_id", "user_id", "category_kind"], _CAT_FK_COLS, name="fk_budgets_category", deferrable=True, initially="DEFERRED"),
        ck("category_kind_expense", "category_kind = 'expense'"),
        ck("month_first_day", "EXTRACT(day FROM month) = 1"),
        positive("limit_amount"),
        ck("alert_threshold_range", "alert_threshold > 0 AND alert_threshold <= 1"),
        Index("ix_budgets_category_fk", "category_id", "user_id"),
    )
