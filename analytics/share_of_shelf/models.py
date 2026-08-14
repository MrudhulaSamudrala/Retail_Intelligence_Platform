"""Data structures for Share of Shelf analytics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class SosScope:
    """Optional filters for a Share of Shelf calculation."""

    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    product_type: Optional[str] = None
    oem: Optional[str] = None
    brand: Optional[str] = None
    as_of: Optional[datetime] = None
    """Used only when ``current_universe`` is False (explicit historical SoS)."""
    collection_run_ids: Optional[tuple[int, ...]] = None
    """When set, SoS uses snapshots/search rows for these run IDs only."""
    current_universe: bool = True
    """If True, candidates come from the latest stratified collection batch
    per retailer/country. Historical products and keyword searches are omitted."""


@dataclass(frozen=True)
class SosShare:
    """One brand or OEM share within an eligible universe."""

    dimension: str  # "brand" | "oem"
    value: str
    product_count: int
    universe_size: int
    share: Decimal  # 0..1


@dataclass
class SosExclusionBreakdown:
    """Why candidate rows were excluded from the eligible universe."""

    accessory_or_ineligible_type: int = 0
    non_gaming: int = 0
    missing_identity: int = 0
    scope_filtered: int = 0
    inactive: int = 0


@dataclass
class SosSnapshot:
    """Full SoS result for one scope and dimension."""

    scope: SosScope
    dimension: str
    universe_size: int
    inclusion_rules_id: str
    shares: list[SosShare] = field(default_factory=list)
    exclusions: SosExclusionBreakdown = field(default_factory=SosExclusionBreakdown)
    as_of: Optional[datetime] = None
    collection_status: str = "NO_DATA"
    """COMPLETE | PARTIAL | NO_DATA. Fallback/ofertas never upgrades to COMPLETE."""
    collection_run_ids: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass(frozen=True)
class SosTrendPoint:
    """SoS for one UTC calendar day (historical trend)."""

    period_start: datetime
    universe_size: int
    shares: tuple[SosShare, ...]
