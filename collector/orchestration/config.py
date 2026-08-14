"""Orchestration config + status constants."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "orchestration.yaml"

STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

COMPONENTS = (
    "newegg",
    "mercadolibre",
    "audits",
    "badges",
    "pricing",
    "banners",
    "search",
)


@dataclass(frozen=True)
class OrchestrationConfig:
    product_limit_per_retailer: int
    search_limit_per_retailer: int
    stale_running_hours: int
    concurrent_lock_key: int
    exit_code_partial: int
    max_attempts: int
    base_delay_seconds: float
    component_timeout_seconds: int
    page_timeout_ms: int


@lru_cache(maxsize=1)
def load_orchestration_config() -> OrchestrationConfig:
    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    orch = data.get("orchestration") or {}
    retries = orch.get("retries") or {}
    timeouts = orch.get("timeouts") or {}
    return OrchestrationConfig(
        product_limit_per_retailer=int(orch.get("product_limit_per_retailer") or 100),
        search_limit_per_retailer=int(orch.get("search_limit_per_retailer") or 3),
        stale_running_hours=int(orch.get("stale_running_hours") or 6),
        concurrent_lock_key=int(orch.get("concurrent_lock_key") or 74628301),
        exit_code_partial=int(orch.get("exit_code_partial") or 0),
        max_attempts=int(retries.get("max_attempts") or 3),
        base_delay_seconds=float(retries.get("base_delay_seconds") or 2),
        component_timeout_seconds=int(timeouts.get("component_seconds") or 1800),
        page_timeout_ms=int(timeouts.get("page_timeout_ms") or 45000),
    )
