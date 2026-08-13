"""DTOs for product visibility analytics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class VisibilityScope:
    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    keyword: Optional[str] = None
    brand: Optional[str] = None
    product_type: Optional[str] = None
    observed_from: Optional[datetime] = None
    observed_to: Optional[datetime] = None
    organic_only: Optional[bool] = None
    # When True, only rows resolved to a ``products`` row (catalog visibility).
    require_linked_product: bool = False
    top_n: int = 20


@dataclass(frozen=True)
class ProductVisibilityRow:
    retailer_code: str
    country_code: Optional[str]
    product_id: Optional[int]
    retailer_sku: Optional[str]
    title: Optional[str]
    brand: Optional[str]
    oem: Optional[str]
    product_type: Optional[str]
    canonical_product_id: Optional[int]
    appearances: int
    top3_appearances: int
    top5_appearances: int
    top10_appearances: int
    top20_appearances: int
    average_rank: Optional[Decimal]
    keywords: tuple[str, ...]
    visibility_score: Decimal


@dataclass(frozen=True)
class CrossRetailerVisibilityRow:
    canonical_product_id: int
    display_name: Optional[str]
    manufacturer_model: Optional[str]
    oem: Optional[str]
    match_status: str
    match_method: Optional[str]
    match_confidence: Optional[Decimal]
    newegg_product_id: Optional[int]
    mercadolibre_product_id: Optional[int]
    newegg_visibility: Optional[ProductVisibilityRow]
    mercadolibre_visibility: Optional[ProductVisibilityRow]
    combined_appearances: int
    combined_visibility_score: Decimal
