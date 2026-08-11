"""Retailer collector protocol and shared DTOs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from collector.browser import BrowserSession
from collector.normalize import NormalizedProduct


@dataclass
class ListingCandidate:
    """Lightweight listing-page hit before product-page enrichment."""

    retailer_sku: str
    source_url: str
    title: Optional[str] = None
    price_text: Optional[str] = None
    list_price_text: Optional[str] = None
    availability_text: Optional[str] = None
    promo_text: Optional[str] = None
    category_raw: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionOutcome:
    success: list[NormalizedProduct] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    skipped_duplicates: list[str] = field(default_factory=list)
    discovered: int = 0
    collection_run_id: Optional[int] = None
    status: Optional[str] = None
    bot_blocked: bool = False


class RetailerCollector(ABC):
    """Interface every retailer adapter must implement."""

    code: str
    country_code: str
    currency: str

    @abstractmethod
    async def discover_listings(
        self,
        session: BrowserSession,
        *,
        limit: int,
    ) -> list[ListingCandidate]:
        """Return unique listing candidates from the retailer scope."""

    @abstractmethod
    async def fetch_product(
        self,
        session: BrowserSession,
        candidate: ListingCandidate,
    ) -> NormalizedProduct:
        """Fetch and parse a product page into a normalized record."""
