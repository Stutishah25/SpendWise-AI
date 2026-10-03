"""Ownership isolation, constraints, money precision, triggers and cascades."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DataError, IntegrityError

from app.models import (AuditLog, Budget, Category, Goal, GoalContribution, LoanPayment, LoanSchedule,
                        LoanScheduleItem, Notification, RecurringRule, Transaction, User)
from app.models.enums import AuditOutcome, GoalStatus, NotificationType, TxnKind
from tests.db.helpers import expect_error, flush_now
from tests.factories import TODAY, h, make_category, make_goal, make_loan, make_user


def txn(u, cat, **kw):
    base = dict(user_id=u.id, category_id=cat.id, kind=cat.kind, amount=D("100.00"), currency="INR",
                transaction_date=TODAY)
    base.update(kw)
    return Transaction(**base)


# ------------------------------------------------------------------ money / decimals
def test_decimal_round_trip_is_exact(session):
    u = make_user(session); c = make_category(session, u)
    t = txn(u, c, amount=D("99999999999999.99"))  # top of NUMERIC(18,4) range at 2dp
    session.add(t); session.flush(); session.expire(t)
    assert t.amount == D("99999999999999.9900") and isinstance(t.amount, D)


def test_no_binary_float_drift(session):
    u = make_user(session); c = make_category(session, u)
    rows = [txn(u, c, amount=D("0.10")), txn(u, c, amount=D("0.20"))]
    session.add_all(rows); session.flush()
    total = session.scalar(text("select sum(amount) from transactions where user_id=:u"), {"u": u.id})
    assert total == D("0.3000")  # 0.1 + 0.2 == 0.3 exactly (floats would give 0.30000000000000004)


def test_amount_overflow_rejected(session):
    u = make_user(session); c = make_category(session, u)
    expect_error(session, DataError, lambda: (session.add(txn(u, c, amount=D("100000000000000.00"))), session.flush()))


def test_user_amounts_limited_to_currency_minor_units(session):
    u = make_user(session); c = make_category(session, u)
    expect_error(session, IntegrityError, lambda: (session.add(txn(u, c, amount=D("10.005"))), session.flush()))
    ok = txn(u, c, amount=D("10.0500")); session.add(ok); session.flush()   # trailing zeros are fine


def test_engine_output_columns_keep_four_decimals(session):
    u = make_user(session)
    ln = make_loan(session, u, emi_amount=D("175.1234"))        # engine output: 4 dp allowed
    assert ln.emi_amount == D("175.1234")


@pytest.mark.parametrize("amount", [D("0"), D("-5.00")])
def test_non_positive_transaction_rejected(session, amount):
    u = make_user(session); c = make_category(session, u)
    expect_error(session, IntegrityError, lambda: (session.add(txn(u, c, amount=amount)), session.flush()))


def test_invalid_enum_value_rejected(session):
    u = make_user(session)
    expect_error(session, DataError, lambda: session.execute(
        text("insert into goals (user_id,name,goal_type,target_amount,currency) values (:u,'x','spaceship',1,'INR')"),
        {"u": u.id}))


def test_unknown_currency_rejected(session):
    u = make_user(session); c = make_category(session, u)
    expect_error(session, IntegrityError, lambda: (session.add(txn(u, c, currency="XXX")), session.flush()))


# ------------------------------------------------------------------ ownership / IDOR at the DB level
def test_cannot_use_another_users_category(session):
    a, b = make_user(session), make_user(session)
    cat_a = make_category(session, a)
    expect_error(session, IntegrityError, lambda: (session.add(txn(b, cat_a)), flush_now(session)))


def test_transaction_kind_must_match_category_kind(session):
    u = make_user(session); income = make_category(session, u, TxnKind.income)
    expect_error(session, IntegrityError, lambda: (session.add(txn(u, income, kind=TxnKind.expense)), flush_now(session)))


def test_budget_only_for_expense_categories_and_own_categories(session):
    a, b = make_user(session), make_user(session)
    inc = make_category(session, a, TxnKind.income); exp_a = make_category(session, a)
    mk = lambda uid, cid, kind=TxnKind.expense: Budget(user_id=uid, category_id=cid, category_kind=kind,
                                                       month=dt.date(2026, 10, 1), limit_amount=D("100"), currency="INR")
    expect_error(session, IntegrityError, lambda: (session.add(mk(a.id, inc.id, TxnKind.income)), flush_now(session)))
    expect_error(session, IntegrityError, lambda: (session.add(mk(b.id, exp_a.id)), flush_now(session)))
    session.add(mk(a.id, exp_a.id)); flush_now(session)


def test_goal_contribution_must_match_goal_owner_and_currency(session):
    a, b = make_user(session), make_user(session)
    g = make_goal(session, a)
    mk = lambda uid, cur: GoalContribution(goal_id=g.id, user_id=uid, currency=cur, amount=D("10"),
                                           contribution_date=TODAY)
    expect_error(session, IntegrityError, lambda: (session.add(mk(b.id, "INR")), session.flush()))   # wrong owner
    expect_error(session, IntegrityError, lambda: (session.add(mk(a.id, "USD")), session.flush()))   # wrong currency
    session.add(mk(a.id, "INR")); session.flush()


def test_loan_payment_cannot_link_foreign_transaction(session):
    a, b = make_user(session), make_user(session)
    loan_b = make_loan(session, b); cat_a = make_category(session, a)
    t_a = txn(a, cat_a); session.add(t_a); session.flush()
    expect_error(session, IntegrityError, lambda: (session.add(LoanPayment(
        loan_id=loan_b.id, user_id=b.id, currency="INR", payment_date=TODAY, amount=D("10"),
        transaction_id=t_a.id)), session.flush()))


def test_notification_cannot_reference_foreign_goal(session):
    a, b = make_user(session), make_user(session)
    g = make_goal(session, a)
    expect_error(session, IntegrityError, lambda: (session.add(Notification(
        user_id=b.id, type=NotificationType.goal_reminder, title="t", body="b", dedupe_key="k", goal_id=g.id)),
        session.flush()))


def test_refresh_token_rotation_link_is_owner_scoped(session):
    a, b = make_user(session), make_user(session)
    ins = lambda uid, rep=None: session.execute(text(
        "insert into refresh_tokens (user_id,family_id,token_hash,expires_at,replaced_by_id) values "
        "(:u,gen_random_uuid(),:h,now()+interval '1 day',:r) returning id"), {"u": uid, "h": h(), "r": rep}).scalar()
    tok_a = ins(a.id)
    expect_error(session, IntegrityError, lambda: ins(b.id, tok_a))


# ------------------------------------------------------------------ constraints / uniqueness
def test_category_names_unique_per_user_case_insensitive(session):
    a, b = make_user(session), make_user(session)
    make_category(session, a, name="Food")
    expect_error(session, IntegrityError, lambda: (session.add(Category(user_id=a.id, kind=TxnKind.expense, name="FOOD")), session.flush()))
    make_category(session, b, name="Food")  # different user: fine


def test_email_unique_case_insensitive(session):
    make_user(session, "Case@Test.Local")
    expect_error(session, IntegrityError, lambda: (session.add(User(email="case@test.local", password_hash="x")), session.flush()))


def test_recurring_instance_is_idempotent(session):
    u = make_user(session); c = make_category(session, u)
    rule = RecurringRule(user_id=u.id, category_id=c.id, kind=c.kind, amount=D("10"), currency="INR",
                         frequency="monthly", start_date=TODAY, next_run_date=TODAY)
    session.add(rule); session.flush()
    session.add(txn(u, c, recurring_rule_id=rule.id)); session.flush()
    expect_error(session, IntegrityError, lambda: (session.add(txn(u, c, recurring_rule_id=rule.id)), session.flush()))


def test_misc_check_constraints(session):
    u = make_user(session)
    bad_goal = lambda **kw: (session.add(Goal(user_id=u.id, name="g", goal_type="other", target_amount=D("10"),
                                              currency="INR", **kw)), session.flush())
    expect_error(session, IntegrityError, lambda: bad_goal(status=GoalStatus.completed))   # completed_at missing
    expect_error(session, IntegrityError, lambda: make_loan(session, u, first_payment_date=TODAY - dt.timedelta(days=1)))
    expect_error(session, IntegrityError, lambda: make_loan(session, u, annual_interest_rate=D("1.5")))
    c = make_category(session, u)
    expect_error(session, IntegrityError, lambda: (session.add(Budget(
        user_id=u.id, category_id=c.id, month=dt.date(2026, 10, 15), limit_amount=D("1"), currency="INR")), session.flush()))
    expect_error(session, IntegrityError, lambda: (session.add(txn(u, c, transaction_date=dt.date(1980, 1, 1))), session.flush()))


# ------------------------------------------------------------------ loans: schedule, payments
def test_loan_schedule_rules(session):
    u = make_user(session); ln = make_loan(session, u)
    sch = LoanSchedule(loan_id=ln.id, user_id=u.id, currency="INR", engine_version="0.0.0", assumptions={},
                       inputs_hash=h(), installment_count=1, total_interest=D("10"), total_payable=D("1010"))
    session.add(sch); session.flush()
    # only one *current* schedule per loan
    expect_error(session, IntegrityError, lambda: (session.add(LoanSchedule(
        loan_id=ln.id, user_id=u.id, currency="INR", engine_version="0.0.0", assumptions={}, inputs_hash=h(),
        installment_count=1, total_interest=D("1"), total_payable=D("2"))), session.flush()))
    item = lambda pay, pr, it: LoanScheduleItem(schedule_id=sch.id, user_id=u.id, installment_number=1, due_date=TODAY,
                                                opening_balance=D("100"), payment_amount=pay, principal_component=pr,
                                                interest_component=it, closing_balance=D("0"))
    expect_error(session, IntegrityError, lambda: (session.add(item(D("100"), D("90"), D("5"))), session.flush()))
    session.add(item(D("100"), D("95"), D("5"))); session.flush()


def test_emi_installment_paid_once(session):
    u = make_user(session); ln = make_loan(session, u)
    pay = lambda: (session.add(LoanPayment(loan_id=ln.id, user_id=u.id, currency="INR", installment_number=1,
                                           payment_date=TODAY, amount=D("100"))), session.flush())
    pay()
    expect_error(session, IntegrityError, pay)


# ------------------------------------------------------------------ triggers
def test_updated_at_is_maintained_by_trigger_not_the_client(session):
    """now() is the transaction timestamp, so we prove the trigger by trying to write an old value."""
    u = make_user(session); g = make_goal(session, u)
    past = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
    session.execute(update(Goal).where(Goal.id == g.id).values(name="renamed", updated_at=past))
    now = session.scalar(text("select now()"))
    after = session.scalar(text("select updated_at from goals where id=:i"), {"i": g.id})
    assert after == now and after > past


def test_goal_current_amount_view_signed_ledger(session):
    u = make_user(session); g = make_goal(session, u, starting_amount=D("100"))
    for kind, amt in (("contribution", "50"), ("contribution", "25"), ("withdrawal", "10")):
        session.add(GoalContribution(goal_id=g.id, user_id=u.id, currency="INR", kind=kind, amount=D(amt),
                                     contribution_date=TODAY))
    session.flush()
    cur = session.scalar(text("select current_amount from v_goal_current_amounts where goal_id=:g"), {"g": g.id})
    assert cur == D("165.0000")


# ------------------------------------------------------------------ deletion behaviour
def test_deleting_goal_nulls_notification_link_but_keeps_owner(session):
    u = make_user(session); g = make_goal(session, u)
    n = Notification(user_id=u.id, type=NotificationType.goal_reminder, title="t", body="b", dedupe_key="g1", goal_id=g.id)
    session.add(n); session.flush()
    session.execute(delete(Goal).where(Goal.id == g.id)); session.expire_all()
    row = session.execute(select(Notification.goal_id, Notification.user_id).where(Notification.id == n.id)).one()
    assert row.goal_id is None and row.user_id == u.id        # ON DELETE SET NULL (goal_id) keeps user_id


def test_used_category_cannot_be_deleted(session):
    u = make_user(session); c = make_category(session, u)
    session.add(txn(u, c)); session.flush()

    def attempt():
        session.execute(delete(Category).where(Category.id == c.id))
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))   # protective FKs are deferred to COMMIT
    expect_error(session, IntegrityError, attempt)


def test_notification_dedupe_key_unique_per_user(session):
    a, b = make_user(session), make_user(session)
    mk = lambda u: Notification(user_id=u.id, type=NotificationType.system, title="t", body="b", dedupe_key="same")
    session.add(mk(a)); session.flush()
    expect_error(session, IntegrityError, lambda: (session.add(mk(a)), session.flush()))
    session.add(mk(b)); session.flush()


def test_audit_log_survives_user_deletion_anonymised(session):
    u = make_user(session)
    log = AuditLog(user_id=u.id, action="auth.login", outcome=AuditOutcome.success, metadata_={"k": "v"})
    session.add(log); session.flush()
    session.execute(delete(User).where(User.id == u.id)); session.expire_all()
    assert session.scalar(select(AuditLog.user_id).where(AuditLog.id == log.id)) is None
    assert session.scalar(select(AuditLog.action).where(AuditLog.id == log.id)) == "auth.login"
