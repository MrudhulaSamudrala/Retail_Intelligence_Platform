"""Share of Voice / search visibility collection (Playwright)."""

from collector.search.config import (
    KeywordTarget,
    load_keyword_targets,
    load_sov_config,
)
from collector.search.models import SearchHit, SearchRunResult
from collector.search.persist import persist_search_run

__all__ = [
    "KeywordTarget",
    "SearchHit",
    "SearchRunResult",
    "load_keyword_targets",
    "load_sov_config",
    "persist_search_run",
    "collect_search_visibility",
    "run_keyword_search",
]


def __getattr__(name: str):
    if name in {"collect_search_visibility", "run_keyword_search"}:
        from collector.search import collect as _collect

        return getattr(_collect, name)
    raise AttributeError(name)
