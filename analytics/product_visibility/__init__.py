"""Product-level visibility analytics (retailer-specific and cross-retailer).

Separate from brand Share of Voice.

Primary universe: search_observations.observation_source = stratified_catalog.
Score formula is unchanged (appearances + top-N + inverse rank). Native
``position`` is used; ``universe_slot`` is never a rank.
"""

from analytics.product_visibility.models import (
    VISIBILITY_SOURCE_KEYWORD_SEARCH,
    VISIBILITY_SOURCE_STRATIFIED_CATALOG,
    CrossRetailerVisibilityRow,
    ProductVisibilityRow,
    VisibilityScope,
)
from analytics.product_visibility.queries import (
    highest_cross_retailer_visibility,
    highest_visibility_by_retailer,
    list_cross_retailer_visibility,
    list_product_visibility,
)

__all__ = [
    "VISIBILITY_SOURCE_KEYWORD_SEARCH",
    "VISIBILITY_SOURCE_STRATIFIED_CATALOG",
    "CrossRetailerVisibilityRow",
    "ProductVisibilityRow",
    "VisibilityScope",
    "highest_cross_retailer_visibility",
    "highest_visibility_by_retailer",
    "list_cross_retailer_visibility",
    "list_product_visibility",
]
