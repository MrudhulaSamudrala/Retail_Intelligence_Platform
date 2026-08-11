"""Load YAML configuration used by collectors and normalizers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config {name} must be a mapping")
    return data


@lru_cache(maxsize=1)
def load_brands() -> dict[str, Any]:
    return _load_yaml("brands.yaml")


@lru_cache(maxsize=1)
def load_oems() -> dict[str, Any]:
    return _load_yaml("oems.yaml")


@lru_cache(maxsize=1)
def load_product_types() -> dict[str, Any]:
    return _load_yaml("product_types.yaml")


@lru_cache(maxsize=1)
def load_retailers() -> dict[str, Any]:
    return _load_yaml("retailers.yaml")


@lru_cache(maxsize=1)
def load_badges() -> dict[str, Any]:
    return _load_yaml("badges.yaml")


def get_retailer(code: str) -> dict[str, Any]:
    retailers = load_retailers().get("retailers", [])
    for item in retailers:
        if item.get("code") == code:
            return item
    raise KeyError(f"Unknown retailer code: {code}")
