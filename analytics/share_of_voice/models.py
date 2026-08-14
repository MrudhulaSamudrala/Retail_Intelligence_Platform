"""Share of Voice analytics data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


# Primary SoV universe. Historical keyword rows stay in the DB but are not mixed in.
SOV_SOURCE_STRATIFIED_CATALOG = "stratified_catalog"
SOV_SOURCE_KEYWORD_SEARCH = "keyword_search"


@dataclass(frozen=True)
class SovScope:
    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    keyword: Optional[str] = None
    observed_from: Optional[datetime] = None
    observed_to: Optional[datetime] = None
    require_complete: bool = False
    """If True, only COMPLETE collections contribute to exact SoV."""
    organic_only: bool | None = None
    top_n: int | None = None
    observation_source: str = SOV_SOURCE_STRATIFIED_CATALOG
    """``stratified_catalog`` (default) or ``keyword_search`` (historical). Never mixed."""
    stratum: Optional[str] = None


@dataclass(frozen=True)
class BrandKeywordMetrics:
    brand: str
    keyword: Optional[str]
    retailer_code: Optional[str]
    country_code: Optional[str]
    present: bool
    appearances: int
    top_n_count: int
    top_n: int
    average_rank: Optional[Decimal]
    rank_observation_count: int
    share_of_voice: Decimal
    total_tracked_appearances: int
    collection_basis: str  # exact | observed_partial | mixed | empty
    stratum: Optional[str] = None


@dataclass
class SovSnapshot:
    scope: SovScope
    total_observations: int
    tracked_appearances: int
    unknown_appearances: int
    complete_searches: int
    partial_searches: int
    failed_searches: int
    metrics: list[BrandKeywordMetrics] = field(default_factory=list)
    collection_basis: str = "exact"
    observation_source: str = SOV_SOURCE_STRATIFIED_CATALOG
    eligible_observations: int = 0
    other_appearances: int = 0
    excluded_observations: int = 0
    duplicate_observations: int = 0
    unique_tracked_skus: int = 0
    stratum_metrics: list[BrandKeywordMetrics] = field(default_factory=list)
    stratum_status: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SovTrendPoint:
    period_start: datetime
    retailer_code: Optional[str]
    country_code: Optional[str]
    keyword: Optional[str]
    brand: str
    appearances: int
    top_n_count: int
    average_rank: Optional[Decimal]
    share_of_voice: Decimal
    total_tracked_appearances: int
    stratum: Optional[str] = None
