"""Cross-retailer product identity (canonical + crosswalk) — analytics layer."""

from analytics.product_identity.matching import (
    MATCHED,
    POSSIBLE_MATCH,
    UNMATCHED,
    ProductFingerprint,
    build_fingerprint,
    extract_manufacturer_model,
    rebuild_cross_retailer_identity,
    score_pair,
)
from analytics.product_identity.queries import (
    AvailabilityMatrixRow,
    RetailerProductCounts,
    crosswalk_summary,
    list_common_products,
    list_retailer_only_products,
    product_availability_matrix,
    retailer_product_counts,
)
from analytics.product_identity.config import load_product_identity_config

__all__ = [
    "MATCHED",
    "POSSIBLE_MATCH",
    "UNMATCHED",
    "ProductFingerprint",
    "AvailabilityMatrixRow",
    "RetailerProductCounts",
    "build_fingerprint",
    "crosswalk_summary",
    "extract_manufacturer_model",
    "list_common_products",
    "list_retailer_only_products",
    "load_product_identity_config",
    "product_availability_matrix",
    "rebuild_cross_retailer_identity",
    "retailer_product_counts",
    "score_pair",
]
