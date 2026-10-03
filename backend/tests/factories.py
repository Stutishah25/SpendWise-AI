"""Minimal builders for valid rows. Hashes are random: these tests exercise the schema, not the engine."""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from decimal import Decimal as D

from sqlalchemy.orm import Session

from app.models import (Category, FinancialProjection, FinancialSnapshot, Goal, Loan, Scenario, ScenarioComparison,
                        ScenarioComparisonItem, ScenarioRun, User, UserPreference)
from app.models.enums import GoalType, LoanType, ProjectionKind, ScenarioType, SnapshotKind, TxnKind

TODAY = dt.date(2026, 10, 1)


def h() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def make_user(s: Session, email: str | None = None) -> User:
    u = User(email=email or f"{uuid.uuid4().hex[:10]}@test.local", password_hash="!not-a-real-hash")
    s.add(u)
    s.flush()
    s.add(UserPreference(user_id=u.id, base_currency="INR", locale="en-IN", default_inflation_rate=D("0.06"),
                         default_expected_return=D("0.12"), default_horizon_months=120,
                         budget_alert_threshold=D("0.8")))
    s.flush()
    return u


def make_category(s, u, kind=TxnKind.expense, name=None) -> Category:
    c = Category(user_id=u.id, kind=kind, name=name or f"cat-{uuid.uuid4().hex[:6]}")
    s.add(c)
    s.flush()
    return c


def make_goal(s, u, currency="INR", **kw) -> Goal:
    g = Goal(user_id=u.id, name="Goal", goal_type=GoalType.laptop, target_amount=D("1000.00"), currency=currency, **kw)
    s.add(g)
    s.flush()
    return g


def make_loan(s, u, **kw) -> Loan:
    base = dict(user_id=u.id, name="Loan", loan_type=LoanType.personal, principal=D("1000.00"), currency="INR",
                annual_interest_rate=D("0.1"), tenure_months=6, start_date=TODAY,
                first_payment_date=TODAY + dt.timedelta(days=30), emi_amount=D("175.00"), emi_engine_version="0.0.0")
    base.update(kw)
    ln = Loan(**base)
    s.add(ln)
    s.flush()
    return ln


STATE = {"income": {}, "expenses": {}, "savings": {}, "debts": [], "goals": [], "investments": []}


def make_snapshot(s, u, currency="INR") -> FinancialSnapshot:
    snap = FinancialSnapshot(user_id=u.id, currency=currency, kind=SnapshotKind.manual, as_of_date=TODAY,
                             monthly_income=D("50000"), monthly_expenses=D("30000"), total_savings=D("10000"),
                             total_debt=D("0"), monthly_debt_service=D("0"), state=dict(STATE),
                             state_schema_version=1, builder_version="0.0.0", state_hash=h())
    s.add(snap)
    s.flush()
    return snap


def make_projection(s, u, snap, kind, horizon=12) -> FinancialProjection:
    p = FinancialProjection(user_id=u.id, snapshot_id=snap.id, currency=snap.currency, kind=kind,
                            horizon_months=horizon, start_date=TODAY, engine_version="0.0.0",
                            assumptions_schema_version=1, assumptions={}, inputs_hash=h(), final_state={})
    s.add(p)
    s.flush()
    return p


ASSUMPTIONS = {"horizon_months": 12, "starting_balance": "10000", "monthly_income": "50000",
               "monthly_expenses": "30000", "inflation_rate": "0.06", "expected_annual_return": "0.12"}


def make_scenario(s, u, stype=ScenarioType.purchase, currency="INR") -> Scenario:
    sc = Scenario(user_id=u.id, name="Scenario", scenario_type=stype, currency=currency,
                  assumptions_schema_version=1, assumptions=dict(ASSUMPTIONS))
    s.add(sc)
    s.flush()
    return sc


def make_run(s, u, snap, scenario=None, baseline=None, horizon=12) -> ScenarioRun:
    scenario = scenario or make_scenario(s, u)
    baseline = baseline or make_projection(s, u, snap, ProjectionKind.baseline, horizon)
    scen_proj = make_projection(s, u, snap, ProjectionKind.scenario, horizon)
    r = ScenarioRun(user_id=u.id, scenario_id=scenario.id, snapshot_id=snap.id, currency=snap.currency,
                    scenario_type=scenario.scenario_type, horizon_months=horizon, start_date=TODAY,
                    engine_version="0.0.0", assumptions_schema_version=1,
                    assumptions={**ASSUMPTIONS, "horizon_months": horizon}, inputs_hash=h(),
                    baseline_projection_id=baseline.id, scenario_projection_id=scen_proj.id, result={})
    s.add(r)
    s.flush()
    return r


def make_comparison(s, u, snap, runs, horizon=12) -> ScenarioComparison:
    cmp = ScenarioComparison(user_id=u.id, name="Compare", snapshot_id=snap.id, currency=snap.currency,
                             horizon_months=horizon, engine_version="0.0.0", inputs_hash=h(), result={})
    s.add(cmp)
    s.flush()
    for i, r in enumerate(runs, start=1):
        s.add(ScenarioComparisonItem(comparison_id=cmp.id, position=i, user_id=u.id, currency=snap.currency,
                                     snapshot_id=snap.id, horizon_months=horizon, scenario_run_id=r.id,
                                     label=f"Scenario {chr(64 + i)}"))
    s.flush()
    return cmp
