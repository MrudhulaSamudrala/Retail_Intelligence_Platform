"""Mercado Libre discovery URL loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "mercadolibre_discovery.yaml"

# Brand-specific queries must not be used for catalog collection.
# Share of Voice keywords.yaml may still contain brand-intent queries.
BRAND_BIASED_COLLECTION_QUERIES = frozenset(
    {
        "notebook intel",
        "notebook ryzen",
        "notebook amd",
        "notebook qualcomm",
        "notebook apple",
        "laptop intel",
        "laptop ryzen",
        "laptop amd",
        "laptop qualcomm",
        "laptop apple",
        "intel gaming laptop",
        "amd gaming laptop",
        "ryzen gaming laptop",
        "qualcomm laptop",
        "apple laptop",
        "macbook gaming",
        "intel desktop",
        "amd desktop",
        "qualcomm tablet",
        "apple tablet",
        "snapdragon gaming",
        "apple gaming",
        "apple gaming laptop",
    }
)

GENERIC_GAMING_QUERIES = frozenset(
    {
        "notebook gamer",
        "pc gamer",
        "workstation gamer",
        "tablet gamer",
        "placa de video gamer",
        "processador gamer",
    }
)


def load_discovery_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def collection_queries(config: dict[str, Any] | None = None) -> list[str]:
    """Primary catalog-collection queries (lowercased)."""
    cfg = config if config is not None else load_discovery_config()
    queries: list[str] = []
    for item in cfg.get("discovery_primary") or []:
        query = str(item.get("query") or "").strip().lower()
        if query:
            queries.append(query)
    return queries
