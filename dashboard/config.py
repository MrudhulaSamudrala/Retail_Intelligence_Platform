"""Load dashboard YAML configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO_ROOT / "config" / "dashboard.yaml"


@lru_cache(maxsize=1)
def load_dashboard_config() -> dict[str, Any]:
    """Return parsed ``config/dashboard.yaml`` (cached)."""
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("config/dashboard.yaml must be a mapping")
    return data


def invalidate_dashboard_config_cache() -> None:
    load_dashboard_config.cache_clear()


def alert_thresholds() -> dict[str, float]:
    cfg = load_dashboard_config().get("alerts") or {}
    return {str(k): float(v) for k, v in cfg.items()}


def display_settings() -> dict[str, Any]:
    return dict(load_dashboard_config().get("display") or {})


def dashboard_meta() -> dict[str, Any]:
    return dict(load_dashboard_config().get("dashboard") or {})
