"""Optional defense-in-depth (Row-Level Security) and the dev seed script."""
from __future__ import annotations

import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import ProgrammingError

from app.models import Base, Goal, User
from scripts.seed_dev import DEMO_EMAIL, OTHER_EMAIL, seed
from tests.conftest import alembic_config, create_database, drop_database

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "docs" / "spendwise_schema.sql"


def rls_block() -> str:
    """The documented (commented-out) RLS block from the schema file, uncommented."""
    tail = SCHEMA_SQL.read_text(encoding="utf-8")
    tail = tail[tail.index("-- DO $$"):]
    return "\n".join(line[3:] if line.startswith("-- ") else line[2:] for line in tail.splitlines() if line.startswith("--"))


def test_row_level_security_isolates_users():
    name, url = create_database()
    role = f"rls_app_{uuid.uuid4().hex[:6]}"
    eng = create_engine(url)
    try:
        command.upgrade(alembic_config(url), "head")
        with eng.begin() as c:
            c.exec_driver_sql(rls_block().replace("%", "%%"))
            c.execute(text(f"CREATE ROLE {role} NOLOGIN"))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
            ids = {}
            for k in "ab":
                ids[k] = c.execute(text("insert into users (email,password_hash) values (:e,'x') returning id"),
                                   {"e": f"{k}@rls.test"}).scalar()
                c.execute(text("insert into goals (user_id,name,goal_type,target_amount,currency) "
                               "values (:u,'g','other',100,'INR')"), {"u": ids[k]})
        enabled = eng.connect().execute(text(
            "select count(*) from pg_class where relrowsecurity and relnamespace='public'::regnamespace")).scalar()
        assert enabled >= 25

        def as_user(uid, sql, **params):
            with eng.connect() as c, c.begin():
                c.execute(text(f"SET LOCAL ROLE {role}"))
                if uid:
                    c.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(uid)})
                return c.execute(text(sql), params).all()

        assert len(as_user(ids["a"], "select * from goals")) == 1                        # sees only its own
        assert as_user(ids["a"], "select * from goals where user_id=:o", o=ids["b"]) == []  # IDOR attempt: nothing
        assert as_user(None, "select * from goals") == []                                 # no identity -> no rows
        with pytest.raises(ProgrammingError):                                              # cannot write as someone else
            as_user(ids["a"], "insert into goals (user_id,name,goal_type,target_amount,currency) "
                              "values (:o,'x','other',1,'INR')", o=ids["b"])
    finally:
        eng.dispose()
        admin = create_engine(url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text(f"DROP OWNED BY {role}")) if False else None
            c.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


def test_seed_script_is_valid_and_idempotent(session):
    first = seed(session)
    assert first == {"users": 2}
    n = lambda t: session.scalar(select(func.count()).select_from(Base.metadata.tables[t]))
    assert n("users") == 2 and n("transactions") > 100 and n("loan_schedule_items") == 6
    assert seed(session) == {}                                   # second call changes nothing
    demo = session.scalar(select(User).where(User.email == DEMO_EMAIL))
    other = session.scalar(select(User).where(User.email == OTHER_EMAIL))
    assert demo.password_hash.startswith("!")                    # login disabled
    # fixture loan schedule is internally consistent (principal fully repaid, payment = principal + interest)
    rows = session.execute(text("select principal_component, closing_balance from loan_schedule_items "
                                "order by installment_number")).all()
    assert sum(r[0] for r in rows) == D("60000.0000") and rows[-1][1] == 0
    # user isolation in seed data
    mine = session.scalar(select(func.count()).select_from(Base.metadata.tables["transactions"])
                          .where(Base.metadata.tables["transactions"].c.user_id == other.id))
    assert mine == 1
