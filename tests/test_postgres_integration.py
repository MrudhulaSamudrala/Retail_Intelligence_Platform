"""Optional integration checks against local PostgreSQL 18.

Skipped automatically when the database is unreachable so offline unit tests
still pass. Never inserts fake production retailer data.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import inspect, text

load_dotenv()


def _postgres_available() -> bool:
    try:
        from database.connection import get_engine, ping

        return ping(get_engine())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="Local PostgreSQL 18 (bridgeai) is not reachable",
)


def test_sqlalchemy_ping_local_postgres() -> None:
    from database.connection import get_engine, ping

    assert ping(get_engine()) is True


def test_alembic_tables_exist_on_local_postgres() -> None:
    from database.connection import get_engine

    engine = get_engine()
    tables = set(inspect(engine).get_table_names())
    expected = {
        "products",
        "product_snapshots",
        "price_history",
        "promotions",
        "retailer_audits",
        "badges",
        "banner_observations",
        "search_observations",
        "collection_runs",
        "alembic_version",
    }
    missing = expected - tables
    assert not missing, f"Missing tables: {sorted(missing)}"


def test_postgres_version_is_18() -> None:
    from database.connection import get_engine

    with get_engine().connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
    assert str(version).startswith("18"), version


def test_connection_settings_target_local_pg18() -> None:
    assert os.getenv("POSTGRES_HOST", "localhost") == "localhost"
    assert os.getenv("POSTGRES_PORT", "5433") == "5433"
    assert os.getenv("POSTGRES_DB", "bridgeai") == "bridgeai"
    assert os.getenv("POSTGRES_USER", "postgres") == "postgres"
