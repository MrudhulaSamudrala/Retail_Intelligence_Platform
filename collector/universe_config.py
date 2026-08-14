"""Stratified gaming-computing search-universe configuration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "search_visibility.yaml"

DEFAULT_UNIVERSE_SIZE = 100
DEFAULT_MAX_PAGES = 8

STRATUM_ORDER = ("notebook", "desktop", "workstation", "tablet", "gpu", "cpu")
STRATUM_BUDGETS = {
    "notebook": 20,
    "desktop": 20,
    "workstation": 20,
    "tablet": 20,
    "gpu": 10,
    "cpu": 10,
}

# Tokens that must not appear in catalog-collection queries.
BRAND_BIASED_QUERY_TOKENS = (
    "intel",
    "amd",
    "ryzen",
    "qualcomm",
    "snapdragon",
    "apple",
    "macbook",
    "threadripper",
    "core i3",
    "core i5",
    "core i7",
    "core i9",
    "core ultra",
)

REQUIRED_STRATUM_QUERIES = {
    "newegg": {
        "notebook": "gaming laptop",
        "desktop": "gaming desktop",
        "workstation": "gaming workstation",
        "tablet": "gaming tablet",
        "gpu": "gaming graphics card",
        "cpu": "gaming processor",
    },
    "mercadolibre": {
        "notebook": "notebook gamer",
        "desktop": "pc gamer",
        "workstation": "workstation gamer",
        "tablet": "tablet gamer",
        "gpu": "placa de video gamer",
        "cpu": "processador gamer",
    },
}


@dataclass(frozen=True)
class StratumSpec:
    name: str
    budget: int
    query: str
    url: str
    product_type_hint: Optional[str] = None
    fallback_url: Optional[str] = None


@dataclass(frozen=True)
class SearchUniverseConfig:
    search_universe_size: int
    max_pages: int
    stop_on_empty_page: bool
    overall_complete_requires_all_strata: bool
    strata_by_retailer: dict[str, tuple[StratumSpec, ...]]
    generic_queries: dict[str, str]


@lru_cache(maxsize=1)
def load_search_universe_config() -> SearchUniverseConfig:
    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    pagination = data.get("pagination") or {}
    gaming = data.get("gaming_universe") or {}
    raw_strata = list(gaming.get("strata") or [])
    strata_by_retailer: dict[str, list[StratumSpec]] = {
        "newegg": [],
        "mercadolibre": [],
    }
    for item in raw_strata:
        name = str(item.get("name") or "").strip().lower()
        budget = int(item.get("budget") or STRATUM_BUDGETS.get(name) or 0)
        queries = item.get("queries") or {}
        urls = item.get("urls") or {}
        fallbacks = item.get("fallback_urls") or {}
        hint = item.get("product_type_hint")
        for retailer in ("newegg", "mercadolibre"):
            query = str(queries.get(retailer) or "").strip()
            if not query:
                continue
            url = str(urls.get(retailer) or "").strip()
            fallback = str(fallbacks.get(retailer) or "").strip() or None
            strata_by_retailer[retailer].append(
                StratumSpec(
                    name=name,
                    budget=budget,
                    query=query,
                    url=url,
                    product_type_hint=str(hint) if hint else None,
                    fallback_url=fallback,
                )
            )
    generic = {
        retailer: (rows[0].query if rows else "")
        for retailer, rows in strata_by_retailer.items()
    }
    legacy = data.get("generic_queries") or {}
    for key, value in legacy.items():
        generic.setdefault(str(key), str(value))
    return SearchUniverseConfig(
        search_universe_size=int(
            gaming.get("total_budget")
            or data.get("search_universe_size")
            or DEFAULT_UNIVERSE_SIZE
        ),
        max_pages=int(pagination.get("max_pages") or DEFAULT_MAX_PAGES),
        stop_on_empty_page=bool(pagination.get("stop_on_empty_page", True)),
        overall_complete_requires_all_strata=bool(
            gaming.get("overall_complete_requires_all_strata", True)
        ),
        strata_by_retailer={
            key: tuple(value) for key, value in strata_by_retailer.items()
        },
        generic_queries={str(k): str(v) for k, v in generic.items() if k and v},
    )


def search_universe_size() -> int:
    return load_search_universe_config().search_universe_size


def generic_query_for(retailer_code: str) -> str:
    """Notebook-stratum query (backward-compatible single-query helper)."""
    cfg = load_search_universe_config()
    default = "gaming laptop" if retailer_code == "newegg" else "notebook gamer"
    strata = cfg.strata_by_retailer.get(retailer_code) or ()
    notebook = next((s for s in strata if s.name == "notebook"), None)
    if notebook:
        return notebook.query
    return cfg.generic_queries.get(retailer_code, default)


def strata_for(retailer_code: str) -> tuple[StratumSpec, ...]:
    return load_search_universe_config().strata_by_retailer.get(retailer_code) or ()


def collection_queries_for(retailer_code: str) -> list[str]:
    return [spec.query for spec in strata_for(retailer_code)]


def allocate_stratum_budgets(
    retailer_code: str,
    *,
    total_limit: int,
    stratum_filter: Optional[str] = None,
) -> list[tuple[StratumSpec, int]]:
    """Assign observation budgets without borrowing across strata."""
    selected: list[StratumSpec] = []
    for spec in strata_for(retailer_code):
        if stratum_filter and spec.name != stratum_filter:
            continue
        selected.append(spec)
    allocated: list[tuple[StratumSpec, int]] = []
    remaining = max(int(total_limit), 0)
    for spec in selected:
        take = min(spec.budget, remaining)
        if take <= 0:
            break
        allocated.append((spec, take))
        remaining -= take
    return allocated


def query_contains_brand_bias(query: str) -> bool:
    blob = f" {str(query or '').strip().lower()} "
    return any(f" {token} " in blob or blob.strip() == token for token in BRAND_BIASED_QUERY_TOKENS)


def assert_queries_are_brand_neutral(queries: Iterable[str]) -> None:
    biased = [q for q in queries if query_contains_brand_bias(q)]
    if biased:
        raise ValueError(f"brand-specific collection queries are not allowed: {biased}")


def validate_universe_config(config: SearchUniverseConfig | None = None) -> dict[str, Any]:
    """Return validation facts used by tests (raises if budgets/queries are wrong)."""
    cfg = config or load_search_universe_config()
    problems: list[str] = []
    if cfg.search_universe_size != 100:
        problems.append(f"total_budget={cfg.search_universe_size}")
    for retailer, expected in REQUIRED_STRATUM_QUERIES.items():
        specs = {s.name: s for s in (cfg.strata_by_retailer.get(retailer) or ())}
        total = sum(s.budget for s in specs.values())
        if total != 100:
            problems.append(f"{retailer} budget sum={total}")
        for name, budget in STRATUM_BUDGETS.items():
            spec = specs.get(name)
            if spec is None:
                problems.append(f"{retailer} missing stratum {name}")
                continue
            if spec.budget != budget:
                problems.append(f"{retailer}.{name} budget={spec.budget}")
            if spec.query.strip().lower() != expected[name]:
                problems.append(f"{retailer}.{name} query={spec.query!r}")
            if query_contains_brand_bias(spec.query):
                problems.append(f"{retailer}.{name} brand-biased query")
    if problems:
        raise ValueError("invalid gaming universe: " + "; ".join(problems))
    return {
        "total_budget": cfg.search_universe_size,
        "stratum_budgets": dict(STRATUM_BUDGETS),
        "queries": {
            retailer: {s.name: s.query for s in specs}
            for retailer, specs in cfg.strata_by_retailer.items()
        },
    }


def stamp_stratum_candidate(
    candidate: Any,
    *,
    stratum: str,
    query: str,
    search_position: int,
    search_page: int,
    universe_slot: int,
    surface: str = "search",
    used_fallback: bool = False,
    product_type_hint: Optional[str] = None,
) -> Any:
    """Attach stratum metadata without changing native search_position semantics."""
    candidate.search_position = search_position
    candidate.search_page = search_page
    candidate.query = query
    raw = dict(getattr(candidate, "raw", None) or {})
    raw["stratum"] = stratum
    raw["universe_slot"] = universe_slot
    raw["ranking_scope"] = "stratum_query"
    raw["discovery_surface"] = surface
    raw["used_fallback"] = used_fallback
    if product_type_hint:
        raw["product_type_hint"] = product_type_hint
    candidate.raw = raw
    return candidate


def paginate_stratum_pages(
    pages: Sequence[Sequence[Any]],
    *,
    budget: int,
) -> list[tuple[Any, int, int]]:
    """Assign native positions (1..n) across pages until budget. Pure helper for tests."""
    out: list[tuple[Any, int, int]] = []
    position = 0
    for page_number, batch in enumerate(pages, start=1):
        for item in batch:
            if position >= budget:
                return out
            position += 1
            out.append((item, position, page_number))
        if not batch:
            break
    return out
