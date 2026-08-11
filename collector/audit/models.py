"""Audit evidence DTOs and check result structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

VALID_RESULTS = frozenset({PASS, FAIL, UNKNOWN})
CHECK_CODES = ("S1", "S2", "P1", "P2", "P3", "P4", "P5")


@dataclass
class ListingEvidence:
    """Evidence captured from a search/listing tile (S1, S2)."""

    title: Optional[str] = None
    tile_text: Optional[str] = None
    badge_texts: list[str] = field(default_factory=list)
    selectors_used: list[str] = field(default_factory=list)
    source_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    available: bool = True


@dataclass
class ProductEvidence:
    """Evidence captured from a product detail page (P1–P5)."""

    title: Optional[str] = None
    specs: dict[str, str] = field(default_factory=dict)
    specs_available: bool = False
    page_text: Optional[str] = None
    badge_texts: list[str] = field(default_factory=list)
    brand_media_signals: list[str] = field(default_factory=list)
    oem_media_signals: list[str] = field(default_factory=list)
    media_inspected: bool = False
    badges_inspected: bool = False
    selectors_used: list[str] = field(default_factory=list)
    source_url: Optional[str] = None
    screenshot_path: Optional[str] = None
    available: bool = True


@dataclass
class AuditContext:
    """Inputs required to evaluate S1–P5 for one product observation."""

    retailer_code: str
    country_code: str
    brand: Optional[str]
    oem: Optional[str] = None
    product_type: Optional[str] = None
    product_id: Optional[int] = None
    collection_run_id: Optional[int] = None
    observed_at: Optional[datetime] = None
    listing: Optional[ListingEvidence] = None
    product: Optional[ProductEvidence] = None


@dataclass
class AuditCheckResult:
    """One independent audit check outcome with preserved evidence."""

    check_code: str
    result: str
    evidence_text: Optional[str] = None
    screenshot_path: Optional[str] = None
    source_url: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.check_code not in CHECK_CODES:
            raise ValueError(f"Invalid check_code: {self.check_code}")
        if self.result not in VALID_RESULTS:
            raise ValueError(f"Invalid result: {self.result}")
