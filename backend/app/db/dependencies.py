from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db.session import make_engine, make_session_factory


engine = make_engine()
SessionLocal = make_session_factory(engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

