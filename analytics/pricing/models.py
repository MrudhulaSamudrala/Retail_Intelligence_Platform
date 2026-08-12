"""Data structures for pricing / promotion analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class PricingScope:
    """Optional filters applied to analytics queries."""

    brand: Optional[str] = None
    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    product_type: Optional[str] = None
    currency: Optional[str] = None
    observed_from: Optional[datetime] = None
    observed_to: Optional[datetime] = None


@dataclass(frozen=True)
class PriceObservation:
    """One priced observation joined to product identity dimensions."""

    product_id: int
    brand: Optional[str]
    retailer_code: str
    country_code: str
    product_type: Optional[str]
    currency: str
    current_price: Optional[Decimal]
    original_price: Optional[Decimal]
    discount_pct: Optional[Decimal]
    promotion_text: Optional[str]
    is_on_promotion: bool
    observed_at: datetime
    source: str = "price_history"  # price_history | product_snapshots


@dataclass
class DimensionPriceSummary:
    """Aggregated prices for one comparison dimension value (brand/retailer/...)."""

    dimension: str
    value: str
    currency: str
    product_count: int
    observation_count: int
    average_price: Optional[Decimal]
    median_price: Optional[Decimal]
    average_discount_pct: Optional[Decimal]
    discounted_product_count: int


@dataclass(frozen=True)
class PriceTimePoint:
    """Daily aggregate for price or discount change-over-time charts."""

    period_start: datetime
    currency: str
    observation_count: int
    average_price: Optional[Decimal]
    average_discount_pct: Optional[Decimal]
    discounted_observation_count: int
