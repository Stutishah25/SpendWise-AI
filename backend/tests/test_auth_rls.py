"""PostgreSQL Row-Level Security (RLS) integration and cross-user isolation tests."""
from __future__ import annotations

import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from alembic import command
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import create_access_token, hash_password
from app.db.dependencies import clear_session_rls_user, get_db, set_session_rls_user
from app.main import app
from app.models import Goal, User
from app.models.enums import GoalType
from tests.conftest import alembic_config, create_database, drop_database

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "docs" / "spendwise_schema.sql"


def rls_block() -> str:
    """The documented RLS block from spendwise_schema.sql, uncommented."""
    tail = SCHEMA_SQL.read_text(encoding="utf-8")
    tail = tail[tail.index("-- DO $$"):]
    return "\n".join(
        line[3:] if line.startswith("-- ") else line[2:]
        for line in tail.splitlines()
        if line.startswith("--")
    )


# Router to demonstrate end-to-end RLS isolation in FastAPI routes
_test_router = APIRouter(prefix="/test-rls", tags=["test-rls"])


@_test_router.get("/goals")
def list_goals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Notice: query does NOT filter by user_id manually; RLS enforces isolation!
    return [{"id": str(g.id), "name": g.name, "user_id": str(g.user_id)} for g in db.scalars(select(Goal)).all()]


@_test_router.get("/goals/search-other")
def search_other_user_goals(
    target_user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # IDOR attempt: client attempts to query another user's goals
    return [{"id": str(g.id), "name": g.name} for g in db.scalars(select(Goal).where(Goal.user_id == target_user_id)).all()]


app.include_router(_test_router)


def test_dependency_sets_session_rls_user(session):
    """Test that get_current_user sets session.info['current_user_id'] and PostgreSQL app.current_user_id."""
    u = User(email="rls_unit@spendwise.test", password_hash=hash_password("Pass123!"), is_active=True)
    session.add(u)
    session.commit()

    token = create_access_token(u.id)
    resolved_user = get_current_user(token=token, db=session)
    assert resolved_user.id == u.id

    # Verify session info and Postgres current_setting
    assert session.info.get("current_user_id") == str(u.id)
    val = session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
    assert val == str(u.id)


def test_rls_enforces_user_isolation_and_prevents_cross_user_access():
    """End-to-end RLS test: verifies app.current_user_id enforces user isolation and prevents IDOR attacks."""
    name, url = create_database()
    role = f"rls_app_{uuid.uuid4().hex[:6]}"
    eng = create_engine(url)

    try:
        command.upgrade(alembic_config(url), "head")
        with eng.begin() as c:
            # Enable RLS on all user-owned tables
            c.exec_driver_sql(rls_block().replace("%", "%%"))
            # Create non-owner runtime application role
            c.execute(text(f"CREATE ROLE {role} NOLOGIN"))
            c.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            c.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))

            # Create User A and User B with goals
            user_a_id = c.execute(
                text("INSERT INTO users (email, password_hash) VALUES ('a@spendwise.test', 'x') RETURNING id")
            ).scalar()
            user_b_id = c.execute(
                text("INSERT INTO users (email, password_hash) VALUES ('b@spendwise.test', 'x') RETURNING id")
            ).scalar()

            c.execute(
                text("INSERT INTO goals (user_id, name, goal_type, target_amount, currency) "
                     "VALUES (:u, 'Goal A', 'laptop', 50000, 'INR')"),
                {"u": user_a_id},
            )
            c.execute(
                text("INSERT INTO goals (user_id, name, goal_type, target_amount, currency) "
                     "VALUES (:u, 'Goal B', 'car', 200000, 'INR')"),
                {"u": user_b_id},
            )

        token_a = create_access_token(user_a_id)
        token_b = create_access_token(user_b_id)

        # Build TestClient with session using role
        conn = eng.connect()
        conn.execute(text(f"SET ROLE {role}"))
        test_session = Session(bind=conn, expire_on_commit=False)

        def override_get_db():
            try:
                yield test_session
            finally:
                clear_session_rls_user(test_session)

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            # User A calls /test-rls/goals -> sees only Goal A
            res_a = test_client.get("/test-rls/goals", headers={"Authorization": f"Bearer {token_a}"})
            assert res_a.status_code == 200
            goals_a = res_a.json()
            assert len(goals_a) == 1
            assert goals_a[0]["name"] == "Goal A"
            assert goals_a[0]["user_id"] == str(user_a_id)

            # User B calls /test-rls/goals -> sees only Goal B
            res_b = test_client.get("/test-rls/goals", headers={"Authorization": f"Bearer {token_b}"})
            assert res_b.status_code == 200
            goals_b = res_b.json()
            assert len(goals_b) == 1
            assert goals_b[0]["name"] == "Goal B"
            assert goals_b[0]["user_id"] == str(user_b_id)

            # Cross-user access attempt (IDOR): User A queries explicitly with User B's user_id
            idor_res = test_client.get(
                f"/test-rls/goals/search-other?target_user_id={user_b_id}",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert idor_res.status_code == 200
            # Under RLS, PostgreSQL returns empty list because user_a cannot see user_b's records!
            assert idor_res.json() == []

        app.dependency_overrides.clear()
        test_session.close()
        conn.close()

        # Cross-user write attempt: User A attempts to write a goal owned by User B
        with eng.connect() as c, c.begin():
            c.execute(text(f"SET LOCAL ROLE {role}"))
            c.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": str(user_a_id)})
            with pytest.raises(ProgrammingError):
                c.execute(
                    text("INSERT INTO goals (user_id, name, goal_type, target_amount, currency) "
                         "VALUES (:u, 'Malicious Goal', 'laptop', 1000, 'INR')"),
                    {"u": str(user_b_id)},
                )

        # Without app.current_user_id set, queries under role return NO rows
        with eng.connect() as c, c.begin():
            c.execute(text(f"SET LOCAL ROLE {role}"))
            assert c.execute(text("SELECT * FROM goals")).all() == []

    finally:
        eng.dispose()
        admin = create_engine(url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()
