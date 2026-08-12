"""Homepage banner tracking (retailer homepage-level, append-only).

Independent of product discovery and Share of Shelf.
"""

from collector.banners.detect import (
    TRACKED_BRANDS,
    DetectedBanner,
    detect_brand_from_evidence,
    extract_badge_text,
    extract_discount_text,
    is_excluded_region,
    process_banner_candidates,
)
from collector.banners.persist import persist_banners

__all__ = [
    "TRACKED_BRANDS",
    "DetectedBanner",
    "detect_brand_from_evidence",
    "extract_badge_text",
    "extract_discount_text",
    "is_excluded_region",
    "persist_banners",
    "process_banner_candidates",
    "collect_homepage_banners",
    "inspect_retailer_homepage",
]


def __getattr__(name: str):
    if name in {"collect_homepage_banners", "inspect_retailer_homepage"}:
        from collector.banners import collect as _collect

        return getattr(_collect, name)
    raise AttributeError(name)
