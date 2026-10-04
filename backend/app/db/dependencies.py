"""Database session dependency and PostgreSQL Row Level Security (RLS) integration."""
from __future__ import annotations

import uuid
from collections.abc import Generator

from sqlalchemy import event, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import make_engine, make_session_factory

import os

engine = make_engine() if os.environ.get("DATABASE_URL") else None
SessionLocal = make_session_factory(engine) if engine else None


def get_session_factory() -> sessionmaker:
    global engine, SessionLocal
    if SessionLocal is None:
        engine = make_engine()
        SessionLocal = make_session_factory(engine)
    return SessionLocal


@event.listens_for(Session, "after_begin")
def _receive_after_begin(session: Session, transaction, connection):
    """Ensure newly began transactions in an authenticated session inherit app.current_user_id."""
    user_id = session.info.get("current_user_id")
    if user_id:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(user_id)},
        )


def set_session_rls_user(db: Session, user_id: uuid.UUID | str) -> None:
    """Set app.current_user_id in session info and for the active transaction."""
    db.info["current_user_id"] = str(user_id)
    db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )


def clear_session_rls_user(db: Session) -> None:
    """Clear app.current_user_id from session info."""
    db.info.pop("current_user_id", None)


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        clear_session_rls_user(db)
        db.close()
