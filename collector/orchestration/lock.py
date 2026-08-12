"""PostgreSQL advisory lock + stale RUNNING cleanup for concurrent-run prevention."""

from __future__ import annotations

import atexit
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from collector.orchestration.config import STATUS_FAILED, STATUS_RUNNING
from database.models import CollectionRun

logger = logging.getLogger("collector.orchestration.lock")

# Keep a process-level handle so atexit can unlock if the with-block is skipped.
_ATEXIT_CONN: Optional[Connection] = None
_ATEXIT_KEY: Optional[int] = None


def _atexit_unlock() -> None:
    global _ATEXIT_CONN, _ATEXIT_KEY
    conn, key = _ATEXIT_CONN, _ATEXIT_KEY
    _ATEXIT_CONN, _ATEXIT_KEY = None, None
    if conn is None or key is None:
        return
    try:
        if not conn.closed:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()
            conn.close()
    except Exception:  # noqa: BLE001
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def mark_stale_running_runs(session: Session, *, stale_hours: int) -> int:
    """Mark abandoned RUNNING production runs as FAILED."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
    rows = (
        session.query(CollectionRun)
        .filter(
            CollectionRun.run_type == "production",
            CollectionRun.status == STATUS_RUNNING,
            CollectionRun.started_at < cutoff,
        )
        .all()
    )
    for row in rows:
        row.status = STATUS_FAILED
        row.completed_at = datetime.now(timezone.utc)
        row.error_message = (
            (row.error_message or "") + "; marked_stale_running"
        ).strip("; ")
    if rows:
        session.commit()
        logger.warning(
            "stale_runs_marked",
            extra={"event": "stale_runs_marked", "count": len(rows)},
        )
    return len(rows)


def has_active_production_run(session: Session) -> Optional[CollectionRun]:
    return (
        session.query(CollectionRun)
        .filter(
            CollectionRun.run_type == "production",
            CollectionRun.status == STATUS_RUNNING,
        )
        .order_by(CollectionRun.started_at.desc())
        .first()
    )


@contextmanager
def production_lock(
    session: Session, *, lock_key: int, stale_hours: int
) -> Generator[bool, None, None]:
    """Try to acquire a production lock. Yields True if acquired.

    Uses a **dedicated** SQLAlchemy connection for ``pg_try_advisory_lock`` so
    the lock is not released when the ORM session commits (pool checkout).

    On SQLite/tests, falls back to checking for an active RUNNING production run.
    """
    global _ATEXIT_CONN, _ATEXIT_KEY
    mark_stale_running_runs(session, stale_hours=stale_hours)
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    lock_conn: Optional[Connection] = None

    if dialect == "postgresql":
        assert isinstance(bind, Engine) or hasattr(bind, "connect")
        engine: Engine = bind if isinstance(bind, Engine) else bind.engine  # type: ignore[attr-defined]
        lock_conn = engine.connect()
        try:
            acquired = bool(
                lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
                ).scalar()
            )
            if not acquired:
                logger.warning(
                    "advisory_lock_busy",
                    extra={"event": "advisory_lock_busy"},
                )
                yield False
                return
            active = has_active_production_run(session)
            if active is not None:
                logger.warning(
                    "active_run_present",
                    extra={"event": "active_run_present", "run_id": active.id},
                )
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
                )
                lock_conn.commit()
                yield False
                return
            _ATEXIT_CONN = lock_conn
            _ATEXIT_KEY = lock_key
            atexit.register(_atexit_unlock)
            try:
                yield True
            finally:
                _ATEXIT_CONN, _ATEXIT_KEY = None, None
                try:
                    atexit.unregister(_atexit_unlock)
                except Exception:  # noqa: BLE001
                    pass
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
                )
                lock_conn.commit()
        finally:
            if lock_conn is not None and not lock_conn.closed:
                lock_conn.close()
    else:
        active = has_active_production_run(session)
        if active is not None:
            yield False
            return
        yield True
