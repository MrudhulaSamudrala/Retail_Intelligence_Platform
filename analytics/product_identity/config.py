"""Config loader for product identity / visibility scoring."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "product_identity.yaml"


@dataclass(frozen=True)
class VisibilityWeights:
    appearances: float = 1.0
    top3: float = 5.0
    top5: float = 3.0
    top10: float = 2.0
    top20: float = 1.0
    inverse_rank: float = 1.0


@dataclass(frozen=True)
class ProductIdentityConfig:
    matched_min: float
    possible_min: float
    title_similarity_cap: float
    require_oem_for_matched: bool
    visibility: VisibilityWeights
    prefer_organic: bool
    use_latest_batch_only: bool


@lru_cache(maxsize=1)
def load_product_identity_config() -> ProductIdentityConfig:
    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    matching = data.get("matching") or {}
    thresholds = matching.get("thresholds") or {}
    vis = data.get("visibility") or {}
    weights = vis.get("weights") or {}
    return ProductIdentityConfig(
        matched_min=float(thresholds.get("matched_min") or 0.80),
        possible_min=float(thresholds.get("possible_min") or 0.50),
        title_similarity_cap=float(matching.get("title_similarity_cap") or 0.45),
        require_oem_for_matched=bool(matching.get("require_oem_for_matched", True)),
        visibility=VisibilityWeights(
            appearances=float(weights.get("appearances") or 1.0),
            top3=float(weights.get("top3") or 5.0),
            top5=float(weights.get("top5") or 3.0),
            top10=float(weights.get("top10") or 2.0),
            top20=float(weights.get("top20") or 1.0),
            inverse_rank=float(weights.get("inverse_rank") or 1.0),
        ),
        prefer_organic=bool(vis.get("prefer_organic", True)),
        use_latest_batch_only=bool(vis.get("use_latest_batch_only", True)),
    )
