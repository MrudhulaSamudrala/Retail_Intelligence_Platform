"""Analytics: compliance, pricing/promotions, Share of Shelf, Share of Voice, trends.

Also: cross-retailer product identity + product-level visibility (separate from brand SoV).
"""

from analytics.product_identity import (
    AvailabilityMatrixRow,
    RetailerProductCounts,
    crosswalk_summary,
    list_common_products,
    list_retailer_only_products,
    product_availability_matrix,
    rebuild_cross_retailer_identity,
    retailer_product_counts,
)
from analytics.product_visibility import (
    CrossRetailerVisibilityRow,
    ProductVisibilityRow,
    VisibilityScope,
    highest_cross_retailer_visibility,
    highest_visibility_by_retailer,
    list_cross_retailer_visibility,
    list_product_visibility,
)
from analytics.share_of_voice import (
    SovScope,
    SovSnapshot,
    keyword_metrics,
    share_of_voice,
    share_of_voice_trends,
)
from analytics.banner_share import (
    BannerShareScope,
    BannerShareSnapshot,
    banner_share_by_brand,
    banner_share_trends,
)
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
    # Product identity / cross-retailer
    "AvailabilityMatrixRow",
    "RetailerProductCounts",
    "crosswalk_summary",
    "list_common_products",
    "list_retailer_only_products",
    "product_availability_matrix",
    "rebuild_cross_retailer_identity",
    "retailer_product_counts",
    # Product visibility (not brand SoV)
    "CrossRetailerVisibilityRow",
    "ProductVisibilityRow",
    "VisibilityScope",
    "highest_cross_retailer_visibility",
    "highest_visibility_by_retailer",
    "list_cross_retailer_visibility",
    "list_product_visibility",
    # Share of Voice
    "SovScope",
    "SovSnapshot",
    "keyword_metrics",
    "share_of_voice",
    "share_of_voice_trends",
    # Banner Share
    "BannerShareScope",
    "BannerShareSnapshot",
    "banner_share_by_brand",
    "banner_share_trends",
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
