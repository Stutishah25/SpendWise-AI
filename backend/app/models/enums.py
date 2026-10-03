"""Python mirrors of the PostgreSQL ENUM types (values are the stored labels)."""
from __future__ import annotations

import enum


class _Str(str, enum.Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class TxnKind(_Str):
    income = "income"
    expense = "expense"


class TxnSource(_Str):
    manual = "manual"
    recurring = "recurring"
    import_ = "import"


class RecurrenceFrequency(_Str):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"
    yearly = "yearly"


class GoalType(_Str):
    emergency_fund = "emergency_fund"
    laptop = "laptop"
    education = "education"
    travel = "travel"
    car = "car"
    other = "other"


class GoalStatus(_Str):
    active = "active"
    completed = "completed"
    paused = "paused"
    cancelled = "cancelled"


class GoalEntryKind(_Str):
    contribution = "contribution"
    withdrawal = "withdrawal"


class LoanType(_Str):
    education = "education"
    personal = "personal"
    vehicle = "vehicle"
    home = "home"
    credit_card = "credit_card"
    other = "other"


class LoanStatus(_Str):
    active = "active"
    paid_off = "paid_off"
    cancelled = "cancelled"


class LoanPaymentType(_Str):
    emi = "emi"
    prepayment = "prepayment"
    fee = "fee"


class ContributionFrequency(_Str):
    monthly = "monthly"
    quarterly = "quarterly"
    half_yearly = "half_yearly"
    yearly = "yearly"


class PeriodTiming(_Str):
    start_of_period = "start_of_period"
    end_of_period = "end_of_period"


class InvestmentType(_Str):
    sip = "sip"
    lump_sum = "lump_sum"
    sip_plus_lump_sum = "sip_plus_lump_sum"


class ScenarioType(_Str):
    purchase = "purchase"
    loan = "loan"
    investment_plan = "investment_plan"
    education_plan = "education_plan"
    trip_plan = "trip_plan"
    savings_change = "savings_change"
    income_change = "income_change"
    expense_change = "expense_change"
    custom = "custom"


class ScenarioStatus(_Str):
    draft = "draft"
    active = "active"
    archived = "archived"


class CalcType(_Str):
    emi = "emi"
    loan_amortization = "loan_amortization"
    present_value = "present_value"
    future_value = "future_value"
    compound_interest = "compound_interest"
    sip = "sip"
    lump_sum = "lump_sum"
    inflation_adjusted = "inflation_adjusted"
    savings_projection = "savings_projection"
    opportunity_cost = "opportunity_cost"
    goal_contribution = "goal_contribution"
    twin_projection = "twin_projection"
    scenario_simulation = "scenario_simulation"
    scenario_comparison = "scenario_comparison"


class SnapshotKind(_Str):
    manual = "manual"
    scheduled = "scheduled"
    scenario_baseline = "scenario_baseline"


class ProjectionKind(_Str):
    baseline = "baseline"
    scenario = "scenario"


class AiTask(_Str):
    explain_scenario = "explain_scenario"
    compare_scenarios = "compare_scenarios"
    explain_investment = "explain_investment"
    explain_concept = "explain_concept"
    spending_patterns = "spending_patterns"
    monthly_summary = "monthly_summary"
    goal_insights = "goal_insights"


class AiValidationStatus(_Str):
    passed = "passed"
    failed_fallback_used = "failed_fallback_used"
    skipped = "skipped"


class NotificationType(_Str):
    goal_reminder = "goal_reminder"
    budget_alert = "budget_alert"
    emi_reminder = "emi_reminder"
    monthly_summary = "monthly_summary"
    system = "system"


class NotificationSeverity(_Str):
    info = "info"
    warning = "warning"
    critical = "critical"


class DeliveryChannel(_Str):
    in_app = "in_app"
    email = "email"


class DeliveryStatus(_Str):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class AuthTokenPurpose(_Str):
    email_verification = "email_verification"
    password_reset = "password_reset"


class AuditOutcome(_Str):
    success = "success"
    denied = "denied"
    failure = "failure"
