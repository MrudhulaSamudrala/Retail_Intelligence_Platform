"""Database engine and session helpers.

Reads configuration from environment variables (.env via python-dotenv).
Does not connect to production automatically. Callers must explicitly
create an engine/session when needed.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from database.models import Base

load_dotenv()


def build_database_url() -> str:
    """Build a SQLAlchemy PostgreSQL URL from environment variables."""
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        # Render often provides postgres:// — normalize for SQLAlchemy + psycopg3
        if explicit.startswith("postgres://"):
            explicit = explicit.replace("postgres://", "postgresql+psycopg://", 1)
        elif explicit.startswith("postgresql://") and "+psycopg" not in explicit:
            explicit = explicit.replace("postgresql://", "postgresql+psycopg://", 1)
        return explicit

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "bridgeai")
    user = os.getenv("POSTGRES_USER", "bridgeai")
    password = os.getenv("POSTGRES_PASSWORD", "")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def get_engine(*, echo: bool | None = None, url: str | None = None) -> Engine:
    """Create a SQLAlchemy engine from environment configuration or an explicit URL."""
    if echo is None:
        echo = os.getenv("DB_ECHO", "false").lower() in {"1", "true", "yes"}

    database_url = url or build_database_url()
    kwargs: dict = {"echo": echo, "future": True}

    # Connection pooling applies to PostgreSQL; SQLite (tests) uses StaticPool defaults.
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "5"))
        kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "10"))

    return create_engine(database_url, **kwargs)


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Return a session factory bound to the given or default engine."""
    eng = engine or get_engine()
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all_tables(engine: Engine | None = None) -> None:
    """Create all ORM tables (dev/test helper). Prefer Alembic for real environments."""
    eng = engine or get_engine()
    Base.metadata.create_all(bind=eng)


def drop_all_tables(engine: Engine | None = None) -> None:
    """Drop all ORM tables (test helper only)."""
    eng = engine or get_engine()
    Base.metadata.drop_all(bind=eng)


def ping(engine: Engine | None = None) -> bool:
    """Return True if the database accepts a simple connection/query."""
    eng = engine or get_engine()
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
