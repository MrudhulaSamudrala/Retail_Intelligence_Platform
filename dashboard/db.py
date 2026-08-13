"""Read-only database access for the Streamlit dashboard.

Uses the project's existing engine/session helpers. Never writes.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import text
from sqlalchemy.orm import Session

from database.connection import get_engine, get_session_factory, session_scope


def check_connection() -> tuple[bool, str]:
    """Probe PostgreSQL connectivity. Returns (ok, message)."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Connected"
    except Exception as exc:  # noqa: BLE001 — surface any DB error to UI
        return False, str(exc)


@contextmanager
def read_session() -> Generator[Session, None, None]:
    """Yield a session for read-only analytics queries.

    Commits are harmless for pure SELECTs; the helper still uses session_scope
    so connection pooling matches the rest of the project. Callers must not
    mutate ORM objects.
    """
    with session_scope() as session:
        yield session


def get_read_session_factory():
    """Session factory bound to the shared engine (for tests/injection)."""
    return get_session_factory(get_engine())
