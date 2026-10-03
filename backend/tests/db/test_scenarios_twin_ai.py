"""Scenario engine, digital twin and AI storage: reproducibility, comparability, immutability, cascades."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DataError, IntegrityError, InternalError, ProgrammingError

from app.models import (AiAnalysis, Base, FinancialProjection, FinancialProjectionPoint, FinancialSnapshot,
                        InvestmentSimulation, InvestmentSimulationRun, LoanSchedule, Scenario, ScenarioComparison,
                        ScenarioComparisonItem, ScenarioRun, User)
from app.models.enums import AiTask, AiValidationStatus, InvestmentType, ProjectionKind, ScenarioType
from tests.db.helpers import check_deferred, expect_error, flush_now
from tests.factories import (ASSUMPTIONS, STATE, TODAY, h, make_comparison, make_goal, make_loan, make_projection,
                             make_run, make_scenario, make_snapshot, make_user)

IMMUTABLE = (IntegrityError, InternalError, ProgrammingError)  # integrity_constraint_violation surfaces as one of these


def runs3(s, u, snap):
    base = make_projection(s, u, snap, ProjectionKind.baseline)
    sc = make_scenario(s, u)
    return [make_run(s, u, snap, scenario=sc, baseline=base) for _ in range(3)]


def ai(u, **kw):
    base = dict(user_id=u.id, task=AiTask.explain_concept, context_schema_version=1, context={"concept": "sip"},
                context_hash=h(), prompt_version="1", provider="test", model="test-model", output={"summary": "x"},
                validation_status=AiValidationStatus.passed)
    base.update(kw)
    return AiAnalysis(**base)


# ------------------------------------------------------------------ scenario definition + reproducibility
def test_scenario_assumptions_must_contain_core_keys(session):
    u = make_user(session)
    bad = {k: v for k, v in ASSUMPTIONS.items() if k != "inflation_rate"}
    expect_error(session, IntegrityError, lambda: (session.add(Scenario(
        user_id=u.id, name="s", scenario_type=ScenarioType.loan, currency="INR", assumptions_schema_version=1,
        assumptions=bad)), session.flush()))


def test_run_stores_everything_needed_to_reproduce(session):
    u = make_user(session); snap = make_snapshot(session, u); run = make_run(session, u, snap)
    session.expire(run)
    cols = {"calc_type", "scenario_type", "engine_version", "assumptions", "assumptions_schema_version",
            "inputs_hash", "snapshot_id", "currency", "result", "created_at", "horizon_months", "start_date"}
    assert cols <= set(ScenarioRun.__table__.columns.keys())
    assert run.calc_type.value == "scenario_simulation" and run.assumptions["monthly_income"] == "50000"


def test_same_inputs_same_version_cannot_create_duplicate_run(session):
    u = make_user(session); snap = make_snapshot(session, u); run = make_run(session, u, snap)
    sc = session.get(Scenario, run.scenario_id)
    dup_proj = make_projection(session, u, snap, ProjectionKind.scenario)
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioRun(
        user_id=u.id, scenario_id=sc.id, snapshot_id=snap.id, currency="INR", scenario_type=sc.scenario_type,
        horizon_months=12, start_date=TODAY, engine_version=run.engine_version, assumptions_schema_version=1,
        assumptions=dict(ASSUMPTIONS), inputs_hash=run.inputs_hash, baseline_projection_id=run.baseline_projection_id,
        scenario_projection_id=dup_proj.id, result={})), session.flush()))


def test_run_horizon_must_match_frozen_assumptions(session):
    u = make_user(session); snap = make_snapshot(session, u); sc = make_scenario(session, u)
    base = make_projection(session, u, snap, ProjectionKind.baseline, 12)
    scen = make_projection(session, u, snap, ProjectionKind.scenario, 12)
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioRun(
        user_id=u.id, scenario_id=sc.id, snapshot_id=snap.id, currency="INR", scenario_type=sc.scenario_type,
        horizon_months=12, start_date=TODAY, engine_version="0.0.0", assumptions_schema_version=1,
        assumptions={**ASSUMPTIONS, "horizon_months": 24}, inputs_hash=h(), baseline_projection_id=base.id,
        scenario_projection_id=scen.id, result={})), session.flush()))


def test_run_requires_baseline_and_scenario_projection_kinds_and_horizon(session):
    u = make_user(session); snap = make_snapshot(session, u); sc = make_scenario(session, u)
    base = make_projection(session, u, snap, ProjectionKind.baseline)
    scen = make_projection(session, u, snap, ProjectionKind.scenario)
    other_h = make_projection(session, u, snap, ProjectionKind.scenario, 24)
    mk = lambda b, s_: (session.add(ScenarioRun(
        user_id=u.id, scenario_id=sc.id, snapshot_id=snap.id, currency="INR", scenario_type=sc.scenario_type,
        horizon_months=12, start_date=TODAY, engine_version="0.0.0", assumptions_schema_version=1,
        assumptions=dict(ASSUMPTIONS), inputs_hash=h(), baseline_projection_id=b.id,
        scenario_projection_id=s_.id, result={})), session.flush())
    expect_error(session, IntegrityError, lambda: mk(scen, base))       # swapped kinds
    expect_error(session, IntegrityError, lambda: mk(base, other_h))    # horizon mismatch
    mk(base, scen)


def test_run_projections_must_belong_to_the_same_snapshot(session):
    u = make_user(session); s1, s2 = make_snapshot(session, u), make_snapshot(session, u)
    sc = make_scenario(session, u)
    base2 = make_projection(session, u, s2, ProjectionKind.baseline)
    scen1 = make_projection(session, u, s1, ProjectionKind.scenario)
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioRun(
        user_id=u.id, scenario_id=sc.id, snapshot_id=s1.id, currency="INR", scenario_type=sc.scenario_type,
        horizon_months=12, start_date=TODAY, engine_version="0.0.0", assumptions_schema_version=1,
        assumptions=dict(ASSUMPTIONS), inputs_hash=h(), baseline_projection_id=base2.id,
        scenario_projection_id=scen1.id, result={})), flush_now(session)))


def test_run_cannot_use_foreign_snapshot_or_scenario(session):
    a, b = make_user(session), make_user(session)
    snap_a = make_snapshot(session, a); sc_b = make_scenario(session, b)
    base = make_projection(session, a, snap_a, ProjectionKind.baseline)
    scen = make_projection(session, a, snap_a, ProjectionKind.scenario)
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioRun(
        user_id=a.id, scenario_id=sc_b.id, snapshot_id=snap_a.id, currency="INR", scenario_type=sc_b.scenario_type,
        horizon_months=12, start_date=TODAY, engine_version="0.0.0", assumptions_schema_version=1,
        assumptions=dict(ASSUMPTIONS), inputs_hash=h(), baseline_projection_id=base.id,
        scenario_projection_id=scen.id, result={})), session.flush()))


def test_run_currency_must_match_scenario_and_snapshot(session):
    u = make_user(session); snap = make_snapshot(session, u, "USD"); sc = make_scenario(session, u, currency="INR")
    base = make_projection(session, u, snap, ProjectionKind.baseline)
    scen = make_projection(session, u, snap, ProjectionKind.scenario)
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioRun(
        user_id=u.id, scenario_id=sc.id, snapshot_id=snap.id, currency="USD", scenario_type=sc.scenario_type,
        horizon_months=12, start_date=TODAY, engine_version="0.0.0", assumptions_schema_version=1,
        assumptions=dict(ASSUMPTIONS), inputs_hash=h(), baseline_projection_id=base.id,
        scenario_projection_id=scen.id, result={})), session.flush()))


def test_scenario_type_is_pinned_once_runs_exist(session):
    u = make_user(session); snap = make_snapshot(session, u); run = make_run(session, u, snap)
    expect_error(session, IntegrityError, lambda: session.execute(
        update(Scenario).where(Scenario.id == run.scenario_id).values(scenario_type=ScenarioType.loan)))


def test_currency_is_not_hardcoded_other_currencies_work(session):
    u = make_user(session)
    for cur in ("USD", "EUR", "GBP"):
        snap = make_snapshot(session, u, cur); make_goal(session, u, currency=cur)
        run = make_run(session, u, snap, scenario=make_scenario(session, u, currency=cur))
        assert run.currency == cur


# ------------------------------------------------------------------ comparisons (2-3, comparable)
def test_comparison_with_two_or_three_runs_is_valid(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    make_comparison(session, u, snap, r[:2]); check_deferred(session)
    make_comparison(session, u, snap, r); check_deferred(session)


def test_comparison_cannot_hold_a_fourth_item(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    cmp = make_comparison(session, u, snap, r)
    extra = make_run(session, u, snap)
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioComparisonItem(
        comparison_id=cmp.id, position=4, user_id=u.id, currency="INR", snapshot_id=snap.id, horizon_months=12,
        scenario_run_id=extra.id, label="D")), session.flush()))
    expect_error(session, IntegrityError, lambda: (session.add(ScenarioComparisonItem(   # position taken
        comparison_id=cmp.id, position=3, user_id=u.id, currency="INR", snapshot_id=snap.id, horizon_months=12,
        scenario_run_id=extra.id, label="D")), session.flush()))


def test_comparison_needs_at_least_two_items_at_commit(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    make_comparison(session, u, snap, r[:1])
    expect_error(session, IntegrityError, lambda: check_deferred(session))


def test_removing_item_below_two_is_rejected_at_commit(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    cmp = make_comparison(session, u, snap, r[:2]); check_deferred(session)
    session.execute(delete(ScenarioComparisonItem).where(ScenarioComparisonItem.comparison_id == cmp.id,
                                                         ScenarioComparisonItem.position == 2))
    expect_error(session, IntegrityError, lambda: check_deferred(session))


def test_run_run_cannot_appear_twice_in_one_comparison(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    expect_error(session, IntegrityError, lambda: make_comparison(session, u, snap, [r[0], r[0]]))


def test_comparison_rejects_runs_from_other_snapshot_horizon_or_user(session):
    a, b = make_user(session), make_user(session)
    snap = make_snapshot(session, a); other_snap = make_snapshot(session, a)
    ok = make_run(session, a, snap)
    other_snap_run = make_run(session, a, other_snap)
    long_run = make_run(session, a, snap, horizon=24)
    snap_b = make_snapshot(session, b); run_b = make_run(session, b, snap_b)
    for bad in (other_snap_run, long_run, run_b):
        expect_error(session, IntegrityError, lambda bad=bad: (make_comparison(session, a, snap, [ok, bad]), flush_now(session)))


# ------------------------------------------------------------------ immutability (auditability)
def test_result_tables_are_immutable(session):
    u = make_user(session); snap = make_snapshot(session, u); run = make_run(session, u, snap)
    proj = session.get(FinancialProjection, run.scenario_projection_id)
    pt = FinancialProjectionPoint(projection_id=proj.id, month_index=0, user_id=u.id, period_date=TODAY,
                                  income=D("1"), expenses=D("1"), debt_service=D("0"), decision_cash_flow=D("0"),
                                  net_cash_flow=D("0"), savings_balance=D("0"), debt_balance=D("0"),
                                  investment_value=D("0"), net_worth=D("0"))
    session.add(pt); session.flush()
    for stmt in (update(ScenarioRun).where(ScenarioRun.id == run.id).values(result={"hacked": True}),
                 update(ScenarioRun).where(ScenarioRun.id == run.id).values(engine_version="9.9.9"),
                 update(FinancialSnapshot).where(FinancialSnapshot.id == snap.id).values(state={**STATE, "x": 1}),
                 update(FinancialProjection).where(FinancialProjection.id == proj.id).values(final_state={"x": 1}),
                 update(FinancialProjectionPoint).where(FinancialProjectionPoint.projection_id == proj.id)
                 .values(net_worth=D("999"))):
        expect_error(session, IMMUTABLE, lambda stmt=stmt: session.execute(stmt))


def test_comparison_only_name_is_editable(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    cmp = make_comparison(session, u, snap, r[:2])
    session.execute(update(ScenarioComparison).where(ScenarioComparison.id == cmp.id).values(name="Renamed"))
    expect_error(session, IMMUTABLE, lambda: session.execute(
        update(ScenarioComparison).where(ScenarioComparison.id == cmp.id).values(result={"x": 1})))


def test_loan_schedule_only_is_current_flag_can_change(session):
    u = make_user(session); ln = make_loan(session, u)
    sch = LoanSchedule(loan_id=ln.id, user_id=u.id, currency="INR", engine_version="0.0.0", assumptions={},
                       inputs_hash=h(), installment_count=1, total_interest=D("1"), total_payable=D("2"))
    session.add(sch); session.flush()
    session.execute(update(LoanSchedule).where(LoanSchedule.id == sch.id).values(is_current=False))
    expect_error(session, IMMUTABLE, lambda: session.execute(
        update(LoanSchedule).where(LoanSchedule.id == sch.id).values(total_interest=D("5"))))


# ------------------------------------------------------------------ digital twin
def test_snapshot_requires_core_state_keys_and_hash_format(session):
    u = make_user(session)
    mk = lambda **kw: (session.add(FinancialSnapshot(**{**dict(
        user_id=u.id, currency="INR", kind="manual", as_of_date=TODAY, monthly_income=D("1"), monthly_expenses=D("1"),
        total_savings=D("0"), total_debt=D("0"), monthly_debt_service=D("0"), state=dict(STATE),
        state_schema_version=1, builder_version="0.0.0", state_hash=h()), **kw})), session.flush())
    expect_error(session, IntegrityError, lambda: mk(state={"income": {}}))
    expect_error(session, IntegrityError, lambda: mk(state_hash="not-a-hash"))
    expect_error(session, IntegrityError, lambda: mk(builder_version="v1"))
    mk()


def test_projection_points_allow_negative_savings_but_not_negative_debt(session):
    u = make_user(session); snap = make_snapshot(session, u)
    proj = make_projection(session, u, snap, ProjectionKind.baseline)
    pt = lambda debt, i=0: FinancialProjectionPoint(
        projection_id=proj.id, month_index=i, user_id=u.id, period_date=TODAY, income=D("1"), expenses=D("1"),
        debt_service=D("0"), decision_cash_flow=D("-80000"), net_cash_flow=D("-80000"), savings_balance=D("-70000"),
        debt_balance=debt, investment_value=D("0"), net_worth=D("-70000"))
    session.add(pt(D("0"))); session.flush()
    expect_error(session, IntegrityError, lambda: (session.add(pt(D("-1"), 1)), session.flush()))


def test_projection_points_must_belong_to_projection_owner(session):
    a, b = make_user(session), make_user(session); snap = make_snapshot(session, a)
    proj = make_projection(session, a, snap, ProjectionKind.baseline)
    expect_error(session, IntegrityError, lambda: (session.add(FinancialProjectionPoint(
        projection_id=proj.id, month_index=0, user_id=b.id, period_date=TODAY, income=D("0"), expenses=D("0"),
        debt_service=D("0"), decision_cash_flow=D("0"), net_cash_flow=D("0"), savings_balance=D("0"),
        debt_balance=D("0"), investment_value=D("0"), net_worth=D("0"))), session.flush()))


# ------------------------------------------------------------------ deletion behaviour
def test_deleting_a_run_removes_its_comparisons_and_unshared_projections(session):
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    cmp = make_comparison(session, u, snap, r[:2])
    cmp_id, run0, scen_proj, shared_base = cmp.id, r[0].id, r[0].scenario_projection_id, r[1].baseline_projection_id
    session.execute(delete(ScenarioRun).where(ScenarioRun.id == run0)); session.expire_all()
    assert session.get(ScenarioComparison, cmp_id) is None                   # comparison would be invalid -> removed
    assert session.scalar(select(func.count()).select_from(ScenarioComparisonItem)
                          .where(ScenarioComparisonItem.comparison_id == cmp_id)) == 0
    assert session.get(FinancialProjection, scen_proj) is None               # its private projection is cleaned up
    assert session.get(FinancialProjection, shared_base) is not None         # shared baseline survives
    check_deferred(session)                                                  # no dangling references remain


def test_shared_projection_survives_deleting_one_of_two_runs(session):
    """Regression: v1.0 cleanup trigger failed when two runs referenced the same projection."""
    u = make_user(session); snap = make_snapshot(session, u)
    base = make_projection(session, u, snap, ProjectionKind.baseline)
    shared = make_projection(session, u, snap, ProjectionKind.scenario)
    mk = lambda: ScenarioRun(user_id=u.id, scenario_id=sc.id, snapshot_id=snap.id, currency="INR",
                             scenario_type=sc.scenario_type, horizon_months=12, start_date=TODAY, engine_version="0.0.0",
                             assumptions_schema_version=1, assumptions=dict(ASSUMPTIONS), inputs_hash=h(),
                             baseline_projection_id=base.id, scenario_projection_id=shared.id, result={})
    sc = make_scenario(session, u); r1 = mk(); r2 = mk(); session.add_all([r1, r2]); session.flush()
    session.execute(delete(ScenarioRun).where(ScenarioRun.id == r1.id))
    assert session.scalar(select(func.count()).select_from(FinancialProjection).where(FinancialProjection.id == shared.id)) == 1
    session.execute(delete(ScenarioRun).where(ScenarioRun.id == r2.id))
    assert session.scalar(select(func.count()).select_from(FinancialProjection).where(FinancialProjection.id == shared.id)) == 0


def test_deleting_scenario_cascades_to_runs_and_ai(session):
    u = make_user(session); snap = make_snapshot(session, u); run = make_run(session, u, snap)
    a = ai(u, task=AiTask.explain_scenario, scenario_run_id=run.id); session.add(a); session.flush()
    scenario_id, run_id, ai_id = run.scenario_id, run.id, a.id
    session.execute(delete(Scenario).where(Scenario.id == scenario_id)); session.expire_all()
    assert session.get(ScenarioRun, run_id) is None and session.get(AiAnalysis, ai_id) is None
    check_deferred(session)


def test_snapshot_in_use_cannot_be_deleted(session):
    u = make_user(session); snap = make_snapshot(session, u); make_run(session, u, snap)
    def attempt():
        session.execute(delete(FinancialSnapshot).where(FinancialSnapshot.id == snap.id))
        check_deferred(session)                                   # protective FKs are checked at COMMIT
    expect_error(session, IntegrityError, attempt)


def test_deleting_a_user_removes_the_whole_graph(session):
    """One user with data in every module; deleting the user must leave no rows behind."""
    u, other = make_user(session), make_user(session)
    snap = make_snapshot(session, u); r = runs3(session, u, snap); cmp = make_comparison(session, u, snap, r)
    session.add_all([ai(u, task=AiTask.compare_scenarios, comparison_id=cmp.id),
                     ai(u, task=AiTask.explain_scenario, scenario_run_id=r[0].id)])
    sim = InvestmentSimulation(user_id=u.id, name="sip", investment_type=InvestmentType.sip, currency="INR",
                               periodic_contribution=D("500"), expected_annual_return=D("0.1"),
                               inflation_rate=D("0.05"), duration_months=24, start_date=TODAY)
    session.add(sim); session.flush()
    session.add(InvestmentSimulationRun(user_id=u.id, simulation_id=sim.id, currency="INR", calc_type="sip",
                                        engine_version="0.0.0", assumptions_schema_version=1, assumptions={},
                                        inputs_hash=h(), result={}))
    make_goal(session, u); make_loan(session, u); session.flush()
    make_goal(session, other)                                   # must survive
    session.execute(delete(User).where(User.id == u.id)); session.flush()
    check_deferred(session)                                      # deferred protective FKs must also pass at COMMIT
    left = {}
    for t in Base.metadata.sorted_tables:
        if "user_id" in t.c and t.name not in ("audit_log",):
            n = session.scalar(select(func.count()).select_from(t).where(t.c.user_id == u.id))
            if n:
                left[t.name] = n
    assert left == {}
    assert session.scalar(text("select count(*) from goals where user_id=:u"), {"u": other.id}) == 1


# ------------------------------------------------------------------ AI analysis storage
def test_ai_task_requires_matching_source(session):
    u = make_user(session)
    expect_error(session, IntegrityError, lambda: (session.add(ai(u, task=AiTask.explain_scenario)), session.flush()))
    expect_error(session, IntegrityError, lambda: (session.add(ai(u, task=AiTask.compare_scenarios)), session.flush()))
    session.add(ai(u)); session.flush()                         # concept explainers need no source


def test_ai_cannot_attach_to_foreign_or_multiple_sources(session):
    a, b = make_user(session), make_user(session)
    snap_a = make_snapshot(session, a); run_a = make_run(session, a, snap_a)
    expect_error(session, IntegrityError, lambda: (session.add(ai(b, task=AiTask.explain_scenario, scenario_run_id=run_a.id)), session.flush()))
    snap_b = make_snapshot(session, b); r = runs3(session, b, snap_b); cmp = make_comparison(session, b, snap_b, r)
    expect_error(session, IntegrityError, lambda: (session.add(ai(b, task=AiTask.explain_scenario,
                 scenario_run_id=r[0].id, comparison_id=cmp.id)), session.flush()))


def test_ai_records_are_immutable_and_validate_hashes(session):
    u = make_user(session); rec = ai(u); session.add(rec); session.flush()
    expect_error(session, IMMUTABLE, lambda: session.execute(update(AiAnalysis).where(AiAnalysis.id == rec.id).values(output={"x": 1})))
    expect_error(session, IntegrityError, lambda: (session.add(ai(u, context_hash="zz")), session.flush()))
    expect_error(session, IntegrityError, lambda: (session.add(ai(u, input_tokens=-1)), session.flush()))


# ------------------------------------------------------------------ investment simulations
def test_investment_definition_rules(session):
    u = make_user(session)
    mk = lambda **kw: (session.add(InvestmentSimulation(**{**dict(
        user_id=u.id, name="x", investment_type=InvestmentType.sip, currency="INR", periodic_contribution=D("100"),
        expected_annual_return=D("0.1"), inflation_rate=D("0.05"), duration_months=12, start_date=TODAY), **kw})),
        session.flush())
    expect_error(session, IntegrityError, lambda: mk(periodic_contribution=D("0")))                   # sip needs contribution
    expect_error(session, IntegrityError, lambda: mk(investment_type=InvestmentType.lump_sum))          # lump sum needs amount
    expect_error(session, IntegrityError, lambda: mk(alternate_returns=[D("0.1")] * 6))
    expect_error(session, IntegrityError, lambda: mk(compounding_periods_per_year=7))
    mk(alternate_returns=[D("0.08"), D("0.06")])
    stored = session.scalar(select(InvestmentSimulation.alternate_returns).order_by(InvestmentSimulation.created_at.desc()))
    assert stored == [D("0.080000"), D("0.060000")]


# ------------------------------------------------------------------ every immutable table really is immutable
def test_every_immutable_table_rejects_updates(session):
    """Regression for the TG_ARGV bug: triggers declared WITHOUT arguments must still block changes."""
    from app.models import GoalProgressSnapshot, LoanScheduleItem
    u = make_user(session); snap = make_snapshot(session, u); r = runs3(session, u, snap)
    cmp = make_comparison(session, u, snap, r[:2]); goal = make_goal(session, u); ln = make_loan(session, u)
    sch = LoanSchedule(loan_id=ln.id, user_id=u.id, currency="INR", engine_version="0.0.0", assumptions={},
                       inputs_hash=h(), installment_count=1, total_interest=D("1"), total_payable=D("2"))
    session.add(sch); session.flush()
    session.add(LoanScheduleItem(schedule_id=sch.id, user_id=u.id, installment_number=1, due_date=TODAY,
                                 opening_balance=D("2"), payment_amount=D("2"), principal_component=D("1"),
                                 interest_component=D("1"), closing_balance=D("0")))
    gps = GoalProgressSnapshot(goal_id=goal.id, user_id=u.id, currency="INR", snapshot_date=TODAY,
                               current_amount=D("1"), target_amount=D("10"), progress_ratio=D("0.1"),
                               engine_version="0.0.0", assumptions={}, inputs_hash=h())
    sim = InvestmentSimulation(user_id=u.id, name="s", investment_type=InvestmentType.sip, currency="INR",
                               periodic_contribution=D("5"), expected_annual_return=D("0.1"),
                               inflation_rate=D("0.05"), duration_months=12, start_date=TODAY)
    session.add_all([gps, sim]); session.flush()
    isr = InvestmentSimulationRun(user_id=u.id, simulation_id=sim.id, currency="INR", calc_type="sip",
                                  engine_version="0.0.0", assumptions_schema_version=1, assumptions={},
                                  inputs_hash=h(), result={})
    a = ai(u); session.add_all([isr, a]); session.flush()
    updates = {
        "scenario_runs": "update scenario_runs set start_date = start_date + 1",
        "financial_snapshots": "update financial_snapshots set monthly_income = monthly_income + 1",
        "financial_projections": "update financial_projections set horizon_months = 13",
        "scenario_comparison_items": "update scenario_comparison_items set label = 'changed'",
        "scenario_comparisons (result)": "update scenario_comparisons set engine_version = '9.9.9'",
        "goal_progress_snapshots": "update goal_progress_snapshots set current_amount = 2",
        "loan_schedule_items": "update loan_schedule_items set due_date = due_date + 1",
        "loan_schedules": "update loan_schedules set installment_count = 2",
        "investment_simulation_runs": "update investment_simulation_runs set result = jsonb_build_object('x', 1)",
        "ai_analyses": "update ai_analyses set model = 'other'",
    }
    for label, sql in updates.items():
        with pytest.raises(IMMUTABLE, match="immutable"):
            with session.begin_nested():
                session.execute(text(sql))
