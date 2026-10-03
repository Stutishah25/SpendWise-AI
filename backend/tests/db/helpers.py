import pytest
from sqlalchemy import text


def expect_error(session, exc, action):
    """Run `action` in a SAVEPOINT and assert the database rejects it."""
    with pytest.raises(exc):
        with session.begin_nested():
            action()


def check_deferred(session):
    """Fire DEFERRED constraint triggers now (tests never COMMIT)."""
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def flush_now(session):
    """Flush AND run deferred (protective) foreign-key checks, as COMMIT would."""
    session.flush()
    check_deferred(session)
