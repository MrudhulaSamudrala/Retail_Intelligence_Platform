"""Share of Voice / search visibility analytics.

Share of Voice =
  brand search-result appearances /
  total tracked-brand search-result appearances

This is appearance-based (native SERP position), not unique-SKU and not a
fixed 100-slot denominator. UNKNOWN and OTHER are excluded from the
denominator unless configured otherwise. Exact SoV is only claimed when every
configured stratum is COMPLETE; PARTIAL/fallback (including Mercado Libre
ofertas) is observed / partial search visibility.

Primary source: search_observations.observation_source = stratified_catalog.
Historical keyword_search / NULL rows are not mixed into that universe.
"""

from analytics.share_of_voice.models import (
    SOV_SOURCE_KEYWORD_SEARCH,
    SOV_SOURCE_STRATIFIED_CATALOG,
    BrandKeywordMetrics,
    SovScope,
    SovSnapshot,
    SovTrendPoint,
)
from analytics.share_of_voice.queries import (
    brand_presence,
    keyword_metrics,
    share_of_voice,
    share_of_voice_trends,
)

__all__ = [
    "SOV_SOURCE_KEYWORD_SEARCH",
    "SOV_SOURCE_STRATIFIED_CATALOG",
    "BrandKeywordMetrics",
    "SovScope",
    "SovSnapshot",
    "SovTrendPoint",
    "brand_presence",
    "keyword_metrics",
    "share_of_voice",
    "share_of_voice_trends",
]
