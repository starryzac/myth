from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.db.settings import DatabaseSettings


def create_database_engine(database_url: str | None = None) -> Engine:
    settings = DatabaseSettings()
    url = make_url(database_url or settings.database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Only PostgreSQL with the psycopg driver is supported")
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})


@contextmanager
def database_session(engine: Engine) -> Iterator[Session]:
    """One explicit unit of work; errors roll back and are never swallowed."""
    with Session(engine) as session, session.begin():
        yield session
