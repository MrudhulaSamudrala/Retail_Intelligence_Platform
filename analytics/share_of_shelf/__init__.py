"""Share of Shelf (SoS) analytics.

Definition
----------
SoS(brand) = brand_eligible_product_count / total_eligible_universe_size

The eligible universe is built once per query from a consistent inclusion policy
documented in ``analytics/share_of_shelf/universe.py`` and
``docs/methodology.md`` (Share of Shelf). Config lives under
``config/product_types.yaml`` → ``share_of_shelf``.

Brand attribution uses the product ``brand`` field only. An Apple listing with
``brand=Apple`` and ``oem=Apple`` counts **once** toward Brand=Apple — OEM is
never added into the brand numerator. OEM drilldown is a separate slice.
"""

from analytics.share_of_shelf.models import (
    SosExclusionBreakdown,
    SosScope,
    SosShare,
    SosSnapshot,
    SosTrendPoint,
)
from analytics.share_of_shelf.queries import (
    share_of_shelf,
    share_of_shelf_by_brand,
    share_of_shelf_by_oem,
    share_of_shelf_trends,
)
from analytics.share_of_shelf.universe import (
    INCLUSION_RULES_ID,
    SosUniverseConfig,
    build_eligible_universe,
    is_accessory_excluded,
    is_gaming_eligible,
    load_sos_universe_config,
)

__all__ = [
    "INCLUSION_RULES_ID",
    "SosExclusionBreakdown",
    "SosScope",
    "SosShare",
    "SosSnapshot",
    "SosTrendPoint",
    "SosUniverseConfig",
    "build_eligible_universe",
    "is_accessory_excluded",
    "is_gaming_eligible",
    "load_sos_universe_config",
    "share_of_shelf",
    "share_of_shelf_by_brand",
    "share_of_shelf_by_oem",
    "share_of_shelf_trends",
]
