"""Foundation validation tests — no network scraping, no fake business data, no production DB."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml
from sqlalchemy.orm import DeclarativeBase

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATABASE_DIR = ROOT / "database"
DOCS_DIR = ROOT / "docs"

REQUIRED_CONFIGS = [
    "brands.yaml",
    "oems.yaml",
    "retailers.yaml",
    "product_types.yaml",
    "keywords.yaml",
    "badges.yaml",
    "compliance.yaml",
    "banners.yaml",
]

REQUIRED_DOCS = [
    "architecture.md",
    "methodology.md",
    "assumptions.md",
    "clarifications.md",
    "deployment.md",
]

REQUIRED_MODELS = [
    "CollectionRun",
    "Product",
    "ProductSnapshot",
    "PriceHistory",
    "Promotion",
    "RetailerAudit",
    "Badge",
    "BannerObservation",
    "SearchObservation",
]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        files.append(path)
    return files


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_python_syntax(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path))


@pytest.mark.parametrize("name", REQUIRED_CONFIGS)
def test_yaml_configs_load(name: str) -> None:
    path = CONFIG_DIR / name
    assert path.exists(), f"Missing config: {name}"
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert data is not None, f"Empty YAML: {name}"


def test_brands_include_required_names() -> None:
    data = yaml.safe_load((CONFIG_DIR / "brands.yaml").read_text(encoding="utf-8"))
    names = {item["name"] for item in data["brands"]}
    assert names == {"Intel", "AMD", "Qualcomm", "Apple"}


def test_retailers_include_newegg_and_mercadolibre() -> None:
    data = yaml.safe_load((CONFIG_DIR / "retailers.yaml").read_text(encoding="utf-8"))
    codes = {item["code"] for item in data["retailers"]}
    assert codes == {"newegg", "mercadolibre"}


def test_compliance_equal_check_weights_sum_to_one() -> None:
    data = yaml.safe_load((CONFIG_DIR / "compliance.yaml").read_text(encoding="utf-8"))
    weights = data["check_weights"]
    assert set(weights) == {"S1", "S2", "P1", "P2", "P3", "P4", "P5"}
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-3


def test_segment_weights_match_brief() -> None:
    data = yaml.safe_load((CONFIG_DIR / "compliance.yaml").read_text(encoding="utf-8"))
    assert data["segment_weights"]["notebook"] == 0.85
    assert data["segment_weights"]["desktop"] == 0.15


def test_check_aggregation_strategy_is_configurable() -> None:
    data = yaml.safe_load((CONFIG_DIR / "compliance.yaml").read_text(encoding="utf-8"))
    strategy = data["check_aggregation"]["strategy"]
    assert strategy in {
        "equal_check_weights",
        "configured_check_weights",
        "pooled_observations",
    }

@pytest.mark.parametrize("name", REQUIRED_DOCS)
def test_docs_exist(name: str) -> None:
    assert (DOCS_DIR / name).exists()


def test_env_example_has_postgres_placeholders() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
    ):
        assert key in text
    assert "replace_with_your_password" in text
    assert "5433" in text


def test_gitignore_excludes_env_keeps_example() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in text
    assert "!.env.example" in text


def test_sqlalchemy_models_import_and_metadata() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from database import models
    from database.models import Base

    assert issubclass(Base, DeclarativeBase)
    table_names = set(Base.metadata.tables)
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
    }
    assert expected.issubset(table_names)

    for class_name in REQUIRED_MODELS:
        assert hasattr(models, class_name)


def test_connection_url_builder_without_connecting() -> None:
    import os
    import sys

    sys.path.insert(0, str(ROOT))
    # Ensure deterministic URL from discrete fields (no live connection).
    os.environ.pop("DATABASE_URL", None)
    os.environ["POSTGRES_HOST"] = "localhost"
    os.environ["POSTGRES_PORT"] = "5433"
    os.environ["POSTGRES_DB"] = "bridgeai"
    os.environ["POSTGRES_USER"] = "postgres"
    os.environ["POSTGRES_PASSWORD"] = "test_only"

    from database.connection import build_database_url

    url = build_database_url()
    assert url.startswith("postgresql+psycopg://")
    assert "bridgeai" in url
    assert "localhost" in url
    assert ":5433/" in url
    assert "postgres:" in url


def test_repositories_import() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from database.repositories import (
        CollectionRunRepository,
        ObservationRepository,
        ProductRepository,
    )

    assert CollectionRunRepository is not None
    assert ProductRepository is not None
    assert ObservationRepository is not None


def test_observation_tables_have_observed_at() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from database.models import Base

    observation_tables = [
        "product_snapshots",
        "price_history",
        "promotions",
        "retailer_audits",
        "badges",
        "banner_observations",
        "search_observations",
    ]
    for table_name in observation_tables:
        columns = {c.name for c in Base.metadata.tables[table_name].columns}
        assert "observed_at" in columns, f"{table_name} missing observed_at"
