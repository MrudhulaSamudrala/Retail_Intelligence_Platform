"""Analytics: compliance, pricing/promotions, Share of Shelf, Share of Voice, trends."""

from analytics.compliance import (
    AuditScoreRow,
    ComplianceScore,
    ComplianceScoreConfig,
    compute_brand_scores,
    compute_compliance_score,
    compute_country_scores,
    compute_retailer_scores,
    compute_segment_score,
    load_compliance_score_config,
)
from analytics.pricing import (
    DimensionPriceSummary,
    PriceObservation,
    PriceTimePoint,
    PricingScope,
    average_discount,
    average_price_by_brand,
    compare_by_country,
    compare_by_product_type,
    compare_by_retailer,
    count_discounted_products,
    discount_change_over_time,
    list_price_observations,
    list_snapshot_pricing_rows,
    median_price_by_brand,
    price_change_over_time,
)
from analytics.share_of_shelf import (
    SosScope,
    SosSnapshot,
    share_of_shelf,
    share_of_shelf_by_brand,
    share_of_shelf_by_oem,
    share_of_shelf_trends,
)

__all__ = [
    # Compliance
    "AuditScoreRow",
    "ComplianceScore",
    "ComplianceScoreConfig",
    "compute_brand_scores",
    "compute_compliance_score",
    "compute_country_scores",
    "compute_retailer_scores",
    "compute_segment_score",
    "load_compliance_score_config",
    # Pricing / promotions
    "DimensionPriceSummary",
    "PriceObservation",
    "PriceTimePoint",
    "PricingScope",
    "average_discount",
    "average_price_by_brand",
    "compare_by_country",
    "compare_by_product_type",
    "compare_by_retailer",
    "count_discounted_products",
    "discount_change_over_time",
    "list_price_observations",
    "list_snapshot_pricing_rows",
    "median_price_by_brand",
    "price_change_over_time",
    # Share of Shelf
    "SosScope",
    "SosSnapshot",
    "share_of_shelf",
    "share_of_shelf_by_brand",
    "share_of_shelf_by_oem",
    "share_of_shelf_trends",
]
