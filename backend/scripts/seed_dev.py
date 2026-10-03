"""Development seed data. Entirely synthetic; contains NO real personal or financial information.

    DATABASE_URL=postgresql+psycopg://... python -m scripts.seed_dev            # seed (idempotent)
    DATABASE_URL=postgresql+psycopg://... python -m scripts.seed_dev --reset    # delete demo users, re-seed

Seed accounts cannot log in (password_hash is a non-verifiable placeholder). The auth phase will provide
a dev-only way to set a password.

IMPORTANT: this script contains NO financial formulas. The loan schedule below is a pre-computed fixture
(60,000 at 12% p.a. over 6 months) typed in as literals; real schedules will come from the Python
Financial Calculation Engine. Scenario runs / projections / AI analyses are intentionally NOT seeded
because they must be produced by the engine (their schema is exercised by the test-suite instead).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
from decimal import Decimal as D

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import make_engine
from app.models import (Budget, Category, FinancialSnapshot, Goal, GoalContribution, InvestmentSimulation, Loan,
                        LoanPayment, LoanSchedule, LoanScheduleItem, Notification, NotificationPreference,
                        RecurringRule, Scenario, Transaction, User, UserPreference, UserProfile)
from app.models.enums import (GoalType, InvestmentType, LoanPaymentType, LoanType, NotificationSeverity,
                              NotificationType, RecurrenceFrequency, ScenarioType, SnapshotKind, TxnKind, TxnSource)

DEMO_EMAIL = "demo@spendwise.test"
OTHER_EMAIL = "other@spendwise.test"
PLACEHOLDER_HASH = "!seed-account-login-disabled"
CUR = "INR"

EXPENSE_CATEGORIES = ["Rent", "Groceries", "Transport", "Dining", "Utilities", "Entertainment", "Education",
                      "Health", "Shopping", "Subscriptions", "Other"]
INCOME_CATEGORIES = ["Salary", "Freelance", "Other income"]

# Pre-computed fixture: principal 60000, 12% p.a., 6 monthly instalments. (no_, opening, payment, principal, interest, closing)
LOAN_FIXTURE = [
    (1, "60000.00", "10352.90", "9752.90", "600.00", "50247.10"),
    (2, "50247.10", "10352.90", "9850.43", "502.47", "40396.67"),
    (3, "40396.67", "10352.90", "9948.93", "403.97", "30447.74"),
    (4, "30447.74", "10352.90", "10048.42", "304.48", "20399.32"),
    (5, "20399.32", "10352.90", "10148.91", "203.99", "10250.41"),
    (6, "10250.41", "10352.91", "10250.41", "102.50", "0.00"),
]


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, 1) if d.day == 1 else dt.date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def _user(s: Session, email: str, name: str) -> User:
    u = User(email=email, password_hash=PLACEHOLDER_HASH, email_verified_at=func.now())
    s.add(u)
    s.flush()
    s.add(UserProfile(user_id=u.id, full_name=name, display_name=name.split()[0], country_code="IN"))
    s.add(UserPreference(user_id=u.id, base_currency=CUR, locale="en-IN", default_inflation_rate=D("0.06"),
                         default_expected_return=D("0.12"), default_horizon_months=120,
                         budget_alert_threshold=D("0.8"), onboarding_completed=True))
    for t in NotificationType:
        s.add(NotificationPreference(user_id=u.id, notification_type=t, in_app=True,
                                     email=t is NotificationType.monthly_summary, lead_days=3))
    s.flush()
    return u


def _categories(s: Session, u: User) -> dict[str, Category]:
    cats: dict[str, Category] = {}
    for kind, names in ((TxnKind.expense, EXPENSE_CATEGORIES), (TxnKind.income, INCOME_CATEGORIES)):
        for n in names:
            c = Category(user_id=u.id, kind=kind, name=n, system_key=f"{kind.value}.{n.lower().replace(' ', '_')}")
            s.add(c)
            cats[f"{kind.value}:{n}"] = c
    s.flush()
    return cats


def seed(s: Session, today: dt.date | None = None) -> dict[str, int]:
    if s.scalar(select(User.id).where(User.email == DEMO_EMAIL)):
        return {}  # idempotent
    today = today or dt.date.today()
    this_month = today.replace(day=1)
    rnd = random.Random(42)

    demo = _user(s, DEMO_EMAIL, "Demo Student")
    other = _user(s, OTHER_EMAIL, "Isolation Check")
    cats = _categories(s, demo)
    ocats = _categories(s, other)

    # --- 6 months of transactions (whole amounts, 2 dp) ---------------------------------------
    plan = [("Groceries", 4, (300, 900)), ("Transport", 6, (40, 350)), ("Dining", 3, (200, 800)),
            ("Utilities", 1, (900, 1800)), ("Entertainment", 2, (150, 700)), ("Shopping", 1, (500, 3000))]
    for back in range(6, 0, -1):
        m = add_months(this_month, -back)
        s.add(Transaction(user_id=demo.id, category_id=cats["income:Salary"].id, kind=TxnKind.income,
                          amount=D("55000.00"), currency=CUR, transaction_date=m.replace(day=1),
                          description="Monthly stipend (synthetic)", source=TxnSource.recurring))
        s.add(Transaction(user_id=demo.id, category_id=cats["expense:Rent"].id, kind=TxnKind.expense,
                          amount=D("12000.00"), currency=CUR, transaction_date=m.replace(day=2),
                          description="Rent (synthetic)", source=TxnSource.recurring))
        if back % 2 == 0:
            s.add(Transaction(user_id=demo.id, category_id=cats["income:Freelance"].id, kind=TxnKind.income,
                              amount=D(rnd.randrange(3000, 9000, 50)), currency=CUR,
                              transaction_date=m.replace(day=15), description="Freelance project (synthetic)"))
        for cat, count, (lo, hi) in plan:
            for _ in range(count):
                s.add(Transaction(user_id=demo.id, category_id=cats[f"expense:{cat}"].id, kind=TxnKind.expense,
                                  amount=D(rnd.randrange(lo, hi, 10)), currency=CUR,
                                  transaction_date=m.replace(day=rnd.randrange(3, 28)),
                                  description=f"{cat} (synthetic)"))
    s.add(Transaction(user_id=other.id, category_id=ocats["expense:Groceries"].id, kind=TxnKind.expense,
                      amount=D("500.00"), currency=CUR, transaction_date=this_month, description="Other user's data"))

    # --- recurring rules and budgets ------------------------------------------------------------
    for name, kind, cat, amt in (("Monthly stipend", TxnKind.income, "income:Salary", "55000.00"),
                                 ("Rent", TxnKind.expense, "expense:Rent", "12000.00"),
                                 ("Streaming subscription", TxnKind.expense, "expense:Subscriptions", "499.00")):
        s.add(RecurringRule(user_id=demo.id, category_id=cats[cat].id, kind=kind, amount=D(amt), currency=CUR,
                            description=name, frequency=RecurrenceFrequency.monthly, interval_count=1,
                            start_date=add_months(this_month, -6), next_run_date=add_months(this_month, 1)))
    for cat, limit in (("Groceries", "5000.00"), ("Dining", "2500.00"), ("Transport", "1500.00")):
        s.add(Budget(user_id=demo.id, category_id=cats[f"expense:{cat}"].id, month=this_month,
                     limit_amount=D(limit), currency=CUR))
    s.flush()

    # --- goals ----------------------------------------------------------------------------------
    emergency = Goal(user_id=demo.id, name="Emergency fund", goal_type=GoalType.emergency_fund,
                     target_amount=D("150000.00"), starting_amount=D("20000.00"),
                     planned_monthly_contribution=D("5000.00"), currency=CUR, target_date=add_months(this_month, 18))
    laptop = Goal(user_id=demo.id, name="Laptop", goal_type=GoalType.laptop, target_amount=D("80000.00"),
                  planned_monthly_contribution=D("8000.00"), currency=CUR, target_date=add_months(this_month, 10))
    education = Goal(user_id=demo.id, name="Postgraduate course", goal_type=GoalType.education,
                     target_amount=D("200000.00"), planned_monthly_contribution=D("6000.00"), currency=CUR,
                     target_date=add_months(this_month, 30))
    s.add_all([emergency, laptop, education])
    s.flush()
    for back in (3, 2, 1):
        s.add(GoalContribution(goal_id=emergency.id, user_id=demo.id, currency=CUR,
                               amount=D("5000.00"), contribution_date=add_months(this_month, -back)))
    s.add(GoalContribution(goal_id=laptop.id, user_id=demo.id, currency=CUR, amount=D("8000.00"),
                           contribution_date=add_months(this_month, -1)))

    # --- loan with a pre-computed fixture schedule ---------------------------------------------
    loan = Loan(user_id=demo.id, name="Demo education loan", loan_type=LoanType.education, lender="Demo Bank",
                principal=D("60000.00"), currency=CUR, annual_interest_rate=D("0.12"), tenure_months=6,
                start_date=add_months(this_month, -2), first_payment_date=add_months(this_month, -1),
                emi_amount=D("10352.90"), emi_engine_version="0.0.0")
    s.add(loan)
    s.flush()
    assumptions = {"principal": "60000.00", "annual_interest_rate": "0.12", "tenure_months": 6,
                   "source": "seed_fixture_not_engine_output"}
    sched = LoanSchedule(loan_id=loan.id, user_id=demo.id, currency=CUR, engine_version="0.0.0",
                         assumptions=assumptions, inputs_hash=sha(assumptions), installment_count=6,
                         total_interest=D("2117.41"), total_payable=D("62117.41"))
    s.add(sched)
    s.flush()
    for no, opening, pay, princ, intr, closing in LOAN_FIXTURE:
        s.add(LoanScheduleItem(schedule_id=sched.id, user_id=demo.id, installment_number=no,
                               due_date=add_months(this_month, no - 2), opening_balance=D(opening),
                               payment_amount=D(pay), principal_component=D(princ),
                               interest_component=D(intr), closing_balance=D(closing)))
    for no in (1, 2):
        _, _, pay, princ, intr, _ = LOAN_FIXTURE[no - 1]
        s.add(LoanPayment(loan_id=loan.id, user_id=demo.id, currency=CUR, payment_type=LoanPaymentType.emi,
                          installment_number=no, payment_date=add_months(this_month, no - 2),
                          amount=D(pay), principal_paid=D(princ), interest_paid=D(intr)))

    # --- investment simulation definition (results come from the engine later) ------------------
    s.add(InvestmentSimulation(user_id=demo.id, name="Monthly SIP: return variants", investment_type=InvestmentType.sip,
                               currency=CUR, periodic_contribution=D("5000.00"), expected_annual_return=D("0.12"),
                               alternate_returns=[D("0.08"), D("0.06")], inflation_rate=D("0.06"),
                               duration_months=120, start_date=this_month))

    # --- digital-twin snapshot built from the seeded transactions (plain sums, no formulas) -----
    last_full = add_months(this_month, -1)
    rows = s.execute(select(Transaction.kind, func.sum(Transaction.amount)).where(
        Transaction.user_id == demo.id, Transaction.transaction_date >= last_full,
        Transaction.transaction_date < this_month).group_by(Transaction.kind)).all()
    totals = {k: v for k, v in rows}
    state = {"income": {"monthly_total": str(totals.get(TxnKind.income, D(0)))},
             "expenses": {"monthly_total": str(totals.get(TxnKind.expense, D(0)))},
             "savings": {"balance": "20000.00"}, "debts": [{"name": loan.name, "emi": "10352.90"}],
             "goals": [{"name": g.name} for g in (emergency, laptop, education)], "investments": []}
    s.add(FinancialSnapshot(user_id=demo.id, currency=CUR, kind=SnapshotKind.manual, as_of_date=today,
                            monthly_income=totals.get(TxnKind.income, D(0)),
                            monthly_expenses=totals.get(TxnKind.expense, D(0)), total_savings=D("20000.00"),
                            total_debt=D("30447.74"), monthly_debt_service=D("10352.90"), state=state,
                            state_schema_version=1, builder_version="0.0.0", state_hash=sha(state)))

    # --- scenario DEFINITIONS (laptop A/B/C). Runs are created by the engine in a later phase ---
    base = {"horizon_months": 12, "starting_balance": "20000.00", "monthly_income": "55000.00",
            "monthly_expenses": "36000.00", "inflation_rate": "0.06", "expected_annual_return": "0.12"}
    for name, extra in (("Laptop: buy now", {"payment": "cash", "price": "80000.00"}),
                        ("Laptop: buy on EMI", {"payment": "emi", "price": "80000.00", "loan_rate": "0.14",
                                                "tenure_months": 12}),
                        ("Laptop: save for six months", {"payment": "save_first", "price": "80000.00",
                                                          "save_months": 6})):
        s.add(Scenario(user_id=demo.id, name=name, scenario_type=ScenarioType.purchase, currency=CUR,
                       assumptions_schema_version=1, assumptions={**base, **extra}))

    # --- notifications (idempotent via dedupe_key) ---------------------------------------------
    s.add(Notification(user_id=demo.id, type=NotificationType.budget_alert, severity=NotificationSeverity.warning,
                       title="Dining budget at 80%", body="You have reached 80% of this month's Dining budget.",
                       dedupe_key=f"budget:{this_month:%Y-%m}:dining:80"))
    s.add(Notification(user_id=demo.id, type=NotificationType.emi_reminder, severity=NotificationSeverity.info,
                       title="EMI due soon", body="Instalment 3 of the Demo education loan is due next month.",
                       dedupe_key=f"emi:{loan.id}:3"))
    s.flush()
    return {"users": 2}


def counts(s: Session) -> dict[str, int]:
    from app.models import Base
    return {t.name: s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="delete the demo users (cascades) and re-seed")
    args = ap.parse_args()
    engine = make_engine()
    with Session(engine) as s:
        if args.reset:
            s.execute(delete(User).where(User.email.in_([DEMO_EMAIL, OTHER_EMAIL])))
            s.commit()
        created = seed(s)
        s.commit()
        print("seeded" if created else "already seeded (use --reset to rebuild)")
        for k, v in counts(s).items():
            if v:
                print(f"  {k:32s} {v}")


if __name__ == "__main__":
    main()
