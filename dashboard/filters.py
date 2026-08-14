"""Global dashboard filter state and analytics scope builders."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from analytics.banner_share.models import BannerShareScope
from analytics.pricing.models import PricingScope
from analytics.product_visibility.models import VisibilityScope
from analytics.share_of_shelf.models import SosScope
from analytics.share_of_voice.models import SovScope

from dashboard.config import display_settings


@dataclass(frozen=True)
class DashboardFilters:
    """Normalized global filters applied across compatible analytics scopes."""

    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    product_type: Optional[str] = None
    brand: Optional[str] = None
    oem: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    currency: Optional[str] = None
    stratum: Optional[str] = None

    def label_summary(self) -> str:
        parts: list[str] = []
        if self.retailer_code:
            parts.append(f"Retailer={self.retailer_code}")
        if self.country_code:
            parts.append(f"Country={self.country_code}")
        if self.product_type:
            parts.append(f"Type={self.product_type}")
        if self.brand:
            parts.append(f"Brand={self.brand}")
        if self.oem:
            parts.append(f"OEM={self.oem}")
        if self.currency:
            parts.append(f"Currency={self.currency}")
        if self.stratum:
            parts.append(f"Stratum={self.stratum}")
        if self.date_from or self.date_to:
            a = self.date_from.date().isoformat() if self.date_from else "…"
            b = self.date_to.date().isoformat() if self.date_to else "…"
            parts.append(f"Dates={a}→{b}")
        return ", ".join(parts) if parts else "All data (no filters)"


def default_filters() -> DashboardFilters:
    days = int(display_settings().get("default_date_range_days", 30))
    now = datetime.now(timezone.utc)
    return DashboardFilters(
        date_from=now - timedelta(days=days),
        date_to=now,
    )


def clear_filters() -> DashboardFilters:
    return DashboardFilters()


def previous_period(filters: DashboardFilters) -> Optional[DashboardFilters]:
    """Comparable prior window of equal length, or None if dates missing."""
    if filters.date_from is None or filters.date_to is None:
        return None
    span = filters.date_to - filters.date_from
    if span.total_seconds() <= 0:
        return None
    return replace(
        filters,
        date_from=filters.date_from - span,
        date_to=filters.date_from,
    )


def to_pricing_scope(filters: DashboardFilters) -> PricingScope:
    return PricingScope(
        brand=filters.brand,
        retailer_code=filters.retailer_code,
        country_code=filters.country_code,
        product_type=filters.product_type,
        currency=filters.currency,
        observed_from=filters.date_from,
        observed_to=filters.date_to,
    )


def to_sos_scope(filters: DashboardFilters, *, as_of: Optional[datetime] = None) -> SosScope:
    return SosScope(
        retailer_code=filters.retailer_code,
        country_code=filters.country_code,
        product_type=filters.product_type,
        oem=filters.oem,
        brand=filters.brand,
        as_of=as_of or filters.date_to,
    )


def to_sov_scope(filters: DashboardFilters, *, keyword: Optional[str] = None) -> SovScope:
    return SovScope(
        retailer_code=filters.retailer_code,
        country_code=filters.country_code,
        keyword=keyword,
        observed_from=filters.date_from,
        observed_to=filters.date_to,
        require_complete=False,
        stratum=filters.stratum,
    )


def to_banner_scope(filters: DashboardFilters) -> BannerShareScope:
    return BannerShareScope(
        retailer_code=filters.retailer_code,
        country_code=filters.country_code,
        observed_from=filters.date_from,
        observed_to=filters.date_to,
    )


def to_visibility_scope(filters: DashboardFilters) -> VisibilityScope:
    return VisibilityScope(
        retailer_code=filters.retailer_code,
        country_code=filters.country_code,
        brand=filters.brand,
        product_type=filters.product_type,
        observed_from=filters.date_from,
        observed_to=filters.date_to,
        stratum=filters.stratum,
        top_n=10,
    )


def audit_row_matches(row, filters: DashboardFilters) -> bool:
    """Filter compliance ``AuditScoreRow`` instances client-side.

    Current-universe compliance is the latest audit batch, not the header
    collection-period window. Date filters must not drop those rows.
    """
    if filters.retailer_code and row.retailer_code != filters.retailer_code:
        return False
    if filters.country_code and row.country_code != filters.country_code:
        return False
    if filters.product_type and row.product_type != filters.product_type:
        return False
    if filters.brand and (row.brand or "") != filters.brand:
        return False
    return True
