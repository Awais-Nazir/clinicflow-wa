"""Database engine and transaction management."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def get_database_url() -> str:
    """Read and normalize the database URL supplied by the environment."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


@lru_cache
def get_engine() -> Engine:
    """Create one pooled engine per application process."""

    return create_engine(get_database_url(), pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit successful work and roll back failed work."""

    with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        else:
            session.commit()
