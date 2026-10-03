"""Test database fixtures. Each session builds a throw-away database from the Alembic migration, so the
tests validate the *migration* (not metadata.create_all). Each test runs in a rolled-back transaction."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql+psycopg://spendwise:spendwise_dev@localhost:5432/postgres")


def alembic_config(url: str) -> Config:
    os.environ["DATABASE_URL"] = url
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def create_database() -> tuple[str, str]:
    name = f"spendwise_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    return name, make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def drop_database(name: str) -> None:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def migrated_url():
    try:
        name, url = create_database()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL not available: {exc}")
    command.upgrade(alembic_config(url), "head")
    yield url
    drop_database(name)


@pytest.fixture(scope="session")
def engine(migrated_url):
    eng = create_engine(migrated_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    conn = engine.connect()
    outer = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield s
    s.close()
    outer.rollback()
    conn.close()
