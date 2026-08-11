"""Load compliance scoring configuration from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

STRATEGY_EQUAL = "equal_check_weights"
STRATEGY_CONFIGURED = "configured_check_weights"
STRATEGY_POOLED = "pooled_observations"

SUPPORTED_STRATEGIES = frozenset(
    {STRATEGY_EQUAL, STRATEGY_CONFIGURED, STRATEGY_POOLED}
)

CHECK_CODES = ("S1", "S2", "P1", "P2", "P3", "P4", "P5")


@dataclass(frozen=True)
class ComplianceScoreConfig:
    """Resolved scoring policy from ``config/compliance.yaml`` (+ product types)."""

    strategy: str = STRATEGY_EQUAL
    check_weights: dict[str, float] = field(default_factory=dict)
    segment_weights: dict[str, float] = field(
        default_factory=lambda: {"notebook": 0.85, "desktop": 0.15}
    )
    weighted_product_types: frozenset[str] = field(
        default_factory=lambda: frozenset({"notebook", "desktop"})
    )
    exclude_unknown_from_denominator: bool = True

    def weight_for_segment(self, product_type: str) -> float | None:
        if product_type not in self.weighted_product_types:
            return None
        return self.segment_weights.get(product_type)


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config {name} must be a mapping")
    return data


def _weighted_types_from_product_config(data: dict[str, Any]) -> frozenset[str]:
    types = data.get("product_types") or []
    included = {
        item["code"]
        for item in types
        if isinstance(item, dict) and item.get("included_in_compliance_weighting")
    }
    return frozenset(included) if included else frozenset({"notebook", "desktop"})


@lru_cache(maxsize=1)
def load_compliance_score_config() -> ComplianceScoreConfig:
    """Load scoring strategy and brief segment weights from config files."""
    compliance = _load_yaml("compliance.yaml")
    product_types = _load_yaml("product_types.yaml")

    aggregation = compliance.get("check_aggregation") or {}
    strategy = aggregation.get("strategy", STRATEGY_EQUAL)
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(
            f"Unsupported check_aggregation.strategy={strategy!r}; "
            f"expected one of {sorted(SUPPORTED_STRATEGIES)}"
        )

    weights = compliance.get("check_weights") or {}
    check_weights = {str(k): float(v) for k, v in weights.items()}

    segments = compliance.get("segment_weights") or {}
    segment_weights = {str(k): float(v) for k, v in segments.items()}
    if "notebook" not in segment_weights or "desktop" not in segment_weights:
        raise ValueError(
            "compliance.yaml segment_weights must include notebook and desktop"
        )

    unknown = compliance.get("unknown_handling") or {}
    exclude_unknown = bool(unknown.get("exclude_from_score_denominator", True))

    return ComplianceScoreConfig(
        strategy=strategy,
        check_weights=check_weights,
        segment_weights=segment_weights,
        weighted_product_types=_weighted_types_from_product_config(product_types),
        exclude_unknown_from_denominator=exclude_unknown,
    )
