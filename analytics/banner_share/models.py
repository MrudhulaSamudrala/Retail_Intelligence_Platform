"""Banner Share data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class BannerShareScope:
    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    observed_from: Optional[datetime] = None
    observed_to: Optional[datetime] = None


@dataclass(frozen=True)
class BannerShareRow:
    brand: str
    banner_count: int
    total_tracked_banners: int
    banner_share: Decimal  # 0..1
    retailer_code: Optional[str] = None


@dataclass
class BannerShareSnapshot:
    scope: BannerShareScope
    total_observations: int
    total_tracked_banners: int
    unknown_or_ambiguous: int
    shares: list[BannerShareRow] = field(default_factory=list)
    include_unknown_in_denominator: bool = False


@dataclass(frozen=True)
class BannerShareTrendPoint:
    period_start: datetime
    retailer_code: Optional[str]
    brand: str
    banner_count: int
    total_tracked_banners: int
    banner_share: Decimal
