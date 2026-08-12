"""Pricing and promotion analytics over append-only database observations.

Cross-sectional metrics (averages, medians, discounted counts, comparisons) use
the **latest** ``price_history`` row per product within optional filters.

Time-series metrics bucket all matching observations by calendar day (UTC).

Currencies are never converted: comparisons stay within a single currency unless
the caller explicitly omits a currency filter (results are then grouped by
currency).
"""

from analytics.pricing.models import (
    DimensionPriceSummary,
    PriceObservation,
    PriceTimePoint,
    PricingScope,
)
from analytics.pricing.queries import (
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

__all__ = [
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
]
