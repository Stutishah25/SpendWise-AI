from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
                        String, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (Base, Json, CreatedAtMixin, CurrencyCode, Hash64, Money, Rate, Semver,
                             TimestampMixin, UuidPk, between, ck, is_hash, is_semver, json_object,
                             non_negative, pg_enum, positive)
from app.models.enums import CalcType, LoanPaymentType, LoanStatus, LoanType


class Loan(TimestampMixin, Base):
    """An ACTUAL loan. Hypothetical loans live inside scenario assumptions."""

    __tablename__ = "loans"

    id: Mapped[UuidPk]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    loan_type: Mapped[LoanType] = mapped_column(pg_enum(LoanType, "loan_type"))
    lender: Mapped[str | None] = mapped_column(String(100))
    principal: Mapped[Money]
    currency: Mapped[CurrencyCode] = mapped_column(ForeignKey("currencies.code"))
    annual_interest_rate: Mapped[Rate]
    tenure_months: Mapped[int] = mapped_column(Integer)
    moratorium_months: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    start_date: Mapped[dt.date] = mapped_column(Date)
    first_payment_date: Mapped[dt.date] = mapped_column(Date)
    emi_amount: Mapped[Money]  # engine output captured at creation
    emi_engine_version: Mapped[Semver]
    status: Mapped[LoanStatus] = mapped_column(pg_enum(LoanStatus, "loan_status"), server_default=text("'active'"))
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    schedules: Mapped[list[LoanSchedule]] = relationship(viewonly=True)
    payments: Mapped[list[LoanPayment]] = relationship(viewonly=True)

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_loans_id_user_id"),
        UniqueConstraint("id", "user_id", "currency", name="uq_loans_id_user_id_currency"),
        ck("name_not_blank", "char_length(btrim(name)) > 0"),
        positive("principal"),
        between("annual_interest_rate", "0", "1"),
        between("tenure_months", "1", "600"),
        between("moratorium_months", "0", "120"),
        positive("emi_amount"),
        is_semver("emi_engine_version"),
        ck("first_payment_after_start", "first_payment_date >= start_date"),
        ck("closed_at_matches_status", "(status = 'active') = (closed_at IS NULL)"),
        Index("ix_loans_user_status", "user_id", "status"),
    )


class LoanSchedule(CreatedAtMixin, Base):
    """Persisted amortization (header). Rows are produced by the engine; never edited."""

    __tablename__ = "loan_schedules"

    id: Mapped[UuidPk]
    loan_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    calc_type: Mapped[CalcType] = mapped_column(pg_enum(CalcType, "calc_type"),
                                                server_default=text("'loan_amortization'"))
    engine_version: Mapped[Semver]
    assumptions: Mapped[Json]
    inputs_hash: Mapped[Hash64]
    installment_count: Mapped[int] = mapped_column(Integer)
    total_interest: Mapped[Money]
    total_payable: Mapped[Money]
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    loan: Mapped[Loan] = relationship(viewonly=True)
    items: Mapped[list[LoanScheduleItem]] = relationship(viewonly=True, order_by="LoanScheduleItem.installment_number")

    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_loan_schedules_id_user_id"),
        UniqueConstraint("loan_id", "inputs_hash", "engine_version", name="uq_loan_schedules_loan_hash_version"),
        ForeignKeyConstraint(["loan_id", "user_id", "currency"], ["loans.id", "loans.user_id", "loans.currency"],
                             name="fk_loan_schedules_loan", ondelete="CASCADE"),
        ck("calc_type_loan_amortization", "calc_type = 'loan_amortization'"),
        is_semver("engine_version"),
        json_object("assumptions"),
        is_hash("inputs_hash"),
        positive("installment_count"),
        non_negative("total_interest"),
        positive("total_payable"),
        Index("ux_loan_schedules_current", "loan_id", unique=True, postgresql_where=text("is_current")),
    )


class LoanScheduleItem(Base):
    __tablename__ = "loan_schedule_items"

    schedule_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    installment_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID]
    due_date: Mapped[dt.date] = mapped_column(Date)
    opening_balance: Mapped[Money]
    payment_amount: Mapped[Money]
    principal_component: Mapped[Money]
    interest_component: Mapped[Money]
    closing_balance: Mapped[Money]

    schedule: Mapped[LoanSchedule] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(["schedule_id", "user_id"], ["loan_schedules.id", "loan_schedules.user_id"],
                             name="fk_loan_schedule_items_schedule", ondelete="CASCADE"),
        ck("installment_number_min", "installment_number >= 1"),
        non_negative("opening_balance"),
        non_negative("payment_amount"),
        non_negative("principal_component"),
        non_negative("interest_component"),
        non_negative("closing_balance"),
        ck("payment_equals_components", "payment_amount = principal_component + interest_component"),
        Index("ix_loan_items_user_due", "user_id", "due_date"),
    )


class LoanPayment(TimestampMixin, Base):
    """Payments actually made by the user."""

    __tablename__ = "loan_payments"

    id: Mapped[UuidPk]
    loan_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]
    currency: Mapped[CurrencyCode]
    payment_type: Mapped[LoanPaymentType] = mapped_column(pg_enum(LoanPaymentType, "loan_payment_type"),
                                                          server_default=text("'emi'"))
    installment_number: Mapped[int | None] = mapped_column(Integer)
    payment_date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[Money]
    principal_paid: Mapped[Money | None]
    interest_paid: Mapped[Money | None]
    transaction_id: Mapped[uuid.UUID | None]

    loan: Mapped[Loan] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(["loan_id", "user_id", "currency"], ["loans.id", "loans.user_id", "loans.currency"],
                             name="fk_loan_payments_loan", ondelete="CASCADE"),
        ForeignKeyConstraint(["transaction_id", "user_id"], ["transactions.id", "transactions.user_id"],
                             name="fk_loan_payments_transaction", ondelete="SET NULL (transaction_id)"),
        ck("installment_number_min", "installment_number >= 1"),
        positive("amount"),
        non_negative("principal_paid"),
        non_negative("interest_paid"),
        ck("installment_only_for_emi", "payment_type = 'emi' OR installment_number IS NULL"),
        ck("components_within_amount", "COALESCE(principal_paid,0) + COALESCE(interest_paid,0) <= amount"),
        Index("ux_loan_payments_installment", "loan_id", "installment_number", unique=True,
              postgresql_where=text("payment_type = 'emi' AND installment_number IS NOT NULL")),
        Index("ix_loan_payments_loan_date", "loan_id", text("payment_date DESC")),
        Index("ix_loan_payments_user_date", "user_id", text("payment_date DESC")),
        Index("ix_loan_payments_txn", "transaction_id", "user_id", postgresql_where=text("transaction_id IS NOT NULL")),
    )
