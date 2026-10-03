"""Engine/session factory. Configuration comes from the environment only."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def make_engine(url: str | None = None, **kw):
    return create_engine(url or get_database_url(), pool_pre_ping=True, **kw)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
