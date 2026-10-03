"""SpendWise AI ORM models. Importing this package registers every table on Base.metadata."""
from app.models.ai_analysis import AiAnalysis
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.category import Category
from app.models.currency import Currency
from app.models.financial_snapshot import FinancialProjection, FinancialProjectionPoint, FinancialSnapshot
from app.models.goal import Goal, GoalContribution, GoalProgressSnapshot
from app.models.investment import InvestmentSimulation, InvestmentSimulationRun
from app.models.loan import Loan, LoanPayment, LoanSchedule, LoanScheduleItem
from app.models.notification import Notification, NotificationDelivery, NotificationPreference
from app.models.scenario import Scenario, ScenarioComparison, ScenarioComparisonItem, ScenarioRun
from app.models.transaction import Budget, RecurringRule, Transaction
from app.models.user import AuthToken, RefreshToken, User, UserPreference, UserProfile

__all__ = [
    "AiAnalysis", "AuditLog", "AuthToken", "Base", "Budget", "Category", "Currency", "FinancialProjection",
    "FinancialProjectionPoint", "FinancialSnapshot", "Goal", "GoalContribution", "GoalProgressSnapshot",
    "InvestmentSimulation", "InvestmentSimulationRun", "Loan", "LoanPayment", "LoanSchedule",
    "LoanScheduleItem", "Notification", "NotificationDelivery", "NotificationPreference", "RecurringRule",
    "RefreshToken", "Scenario", "ScenarioComparison", "ScenarioComparisonItem", "ScenarioRun", "Transaction",
    "User", "UserPreference", "UserProfile",
]
