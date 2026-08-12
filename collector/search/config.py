"""Load configurable Share of Voice keyword targets from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "keywords.yaml"


@dataclass(frozen=True)
class KeywordTarget:
    retailer_code: str
    country_code: str
    keyword: str
    language: str | None = None


@dataclass(frozen=True)
class SovConfig:
    tracked_brands: tuple[str, ...]
    include_unknown_in_denominator: bool
    top_n_options: tuple[int, ...]
    default_top_n: int
    prefer_organic: bool
    max_pages: int
    max_results_per_keyword: int
    stop_on_empty_page: bool


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("keywords.yaml must be a mapping")
    return data


def load_sov_config() -> SovConfig:
    data = _load_raw()
    sov = data.get("share_of_voice") or {}
    pagination = data.get("pagination") or {}
    top_n = [int(x) for x in (sov.get("top_n_options") or [3, 5, 10, 20])]
    return SovConfig(
        tracked_brands=tuple(
            sov.get("tracked_brands") or ["Intel", "AMD", "Qualcomm", "Apple"]
        ),
        include_unknown_in_denominator=bool(
            sov.get("include_unknown_in_sov_denominator", False)
        ),
        top_n_options=tuple(sorted(set(top_n))),
        default_top_n=int(sov.get("default_top_n") or 10),
        prefer_organic=bool(sov.get("prefer_organic", True)),
        max_pages=int(pagination.get("max_pages") or 3),
        max_results_per_keyword=int(pagination.get("max_results_per_keyword") or 60),
        stop_on_empty_page=bool(pagination.get("stop_on_empty_page", True)),
    )


def load_keyword_targets(
    *,
    retailer_codes: list[str] | None = None,
    country_codes: list[str] | None = None,
    keywords: list[str] | None = None,
    limit_per_retailer: int | None = None,
) -> list[KeywordTarget]:
    """Expand ``retailers.<code>.countries.<CC>.queries`` into flat targets."""
    data = _load_raw()
    targets: list[KeywordTarget] = []

    retailers = data.get("retailers")
    if isinstance(retailers, dict):
        for retailer_code, rcfg in retailers.items():
            if retailer_codes and retailer_code not in retailer_codes:
                continue
            countries = (rcfg or {}).get("countries") or {}
            for country_code, ccfg in countries.items():
                if country_codes and country_code not in country_codes:
                    continue
                language = (ccfg or {}).get("language")
                queries = list((ccfg or {}).get("queries") or [])
                if limit_per_retailer is not None:
                    queries = queries[:limit_per_retailer]
                for q in queries:
                    if keywords and q not in keywords:
                        continue
                    targets.append(
                        KeywordTarget(
                            retailer_code=str(retailer_code),
                            country_code=str(country_code),
                            keyword=str(q),
                            language=str(language) if language else None,
                        )
                    )
        return targets

    legacy = data.get("keywords") or {}
    for retailer_code, rcfg in legacy.items():
        if retailer_codes and retailer_code not in retailer_codes:
            continue
        country_code = str((rcfg or {}).get("country_code") or "")
        if country_codes and country_code not in country_codes:
            continue
        language = (rcfg or {}).get("language")
        queries = list((rcfg or {}).get("queries") or [])
        if limit_per_retailer is not None:
            queries = queries[:limit_per_retailer]
        for q in queries:
            if keywords and q not in keywords:
                continue
            targets.append(
                KeywordTarget(
                    retailer_code=str(retailer_code),
                    country_code=country_code,
                    keyword=str(q),
                    language=str(language) if language else None,
                )
            )
    return targets
