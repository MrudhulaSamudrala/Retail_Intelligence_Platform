"""Banner Share analytics over append-only ``banner_observations``.

Banner Share =
  brand tracked banner observations /
  total tracked-brand banner observations

UNKNOWN / AMBIGUOUS are stored for auditability but excluded from the
tracked-brand denominator unless ``include_unknown_in_banner_share`` is true
in ``config/banners.yaml``.

Independent of Share of Shelf / product universe.
"""

from analytics.banner_share.models import (
    BannerShareRow,
    BannerShareScope,
    BannerShareSnapshot,
    BannerShareTrendPoint,
)
from analytics.banner_share.queries import (
    banner_share_by_brand,
    banner_share_trends,
    load_banner_observations,
)

__all__ = [
    "BannerShareRow",
    "BannerShareScope",
    "BannerShareSnapshot",
    "BannerShareTrendPoint",
    "banner_share_by_brand",
    "banner_share_trends",
    "load_banner_observations",
]
