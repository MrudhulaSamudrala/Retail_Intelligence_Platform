"""Shared report section catalog. Excel and PSV consume the same table keys."""

from __future__ import annotations

# (psv_heading, table_key, excel_sheet_title, chart_value_column or None)
REPORT_SECTIONS: tuple[tuple[str, str, str, str | None], ...] = (
    ("EXECUTIVE SUMMARY", "executive", "Executive Summary", None),
    ("COLLECTION DETAILS", "details", "Collection Details", None),
    ("RETAILER COVERAGE", "coverage", "Retailer Coverage", None),
    ("COLLECTION STATUS", "status", "Collection Status", None),
    ("SHARE OF SHELF", "shelf", "Share of Shelf", "share_percent"),
    ("SEARCH VISIBILITY", "visibility", "Search Visibility", "share_of_voice"),
    ("PRICING", "pricing", "Pricing", "average_price"),
    ("PROMOTIONS", "promotions", "Promotions", "discounted_products"),
    ("PROMOTION DETAILS", "promotion_details", "Promotion Details", None),
    ("BRAND COMPLIANCE", "compliance", "Brand Compliance", "overall"),
    ("BANNER TRACKING", "banners", "Banner Tracking", "share_percent"),
    ("BADGE COVERAGE", "badges", "Badge Coverage", "coverage_percent"),
    ("PRODUCT DATA QUALITY", "quality", "Product Data Quality", "coverage_percent"),
    ("PRODUCT DATA", "products", "Product Data", None),
)

UNAVAILABLE_STATUS = "NOT_AVAILABLE"
UNAVAILABLE_MESSAGE = "Not available for this historical run"


def unavailable_rows() -> list[dict[str, str]]:
    return [{"status": UNAVAILABLE_STATUS, "message": UNAVAILABLE_MESSAGE}]


def is_unavailable(rows: list[dict] | None) -> bool:
    if not rows:
        return True
    first = rows[0]
    return first.get("status") == UNAVAILABLE_STATUS
