"""Search visibility DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_ZERO = "ZERO_RESULTS"


@dataclass
class SearchHit:
    """One search-result slot observation (append-only when persisted)."""

    keyword: str
    retailer_code: str
    country_code: str
    position: int
    page_number: int
    retailer_sku: Optional[str]
    source_url: Optional[str]
    title: Optional[str]
    brand: Optional[str]
    oem: Optional[str] = None
    is_sponsored: bool = False
    evidence_text: Optional[str] = None
    selector: Optional[str] = None
    search_url: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchRunResult:
    """Outcome of one retailer × country × keyword search collection."""

    retailer_code: str
    country_code: str
    keyword: str
    collection_status: str
    pages_collected: int
    hits: list[SearchHit] = field(default_factory=list)
    search_url: Optional[str] = None
    observed_at: Optional[datetime] = None
    error: Optional[str] = None
    pagination_reliable: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def results_collected(self) -> int:
        return len(self.hits)
