"""SQLAlchemy Banner Share queries over ``banner_observations``."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.banner_share.models import (
    BannerShareRow,
    BannerShareScope,
    BannerShareSnapshot,
    BannerShareTrendPoint,
)
from collector.banners.detect import AMBIGUOUS, TRACKED_BRANDS, UNKNOWN, load_banner_config
from database.models import BannerObservation


def _share(count: int, total: int) -> Decimal:
    if total <= 0:
        return Decimal("0")
    return (Decimal(count) / Decimal(total)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _day_start(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def load_banner_observations(
    session: Session,
    *,
    scope: BannerShareScope | None = None,
) -> Sequence[BannerObservation]:
    scope = scope or BannerShareScope()
    stmt = select(BannerObservation).where(BannerObservation.page_type == "homepage")
    if scope.retailer_code is not None:
        stmt = stmt.where(BannerObservation.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        stmt = stmt.where(BannerObservation.country_code == scope.country_code)
    if scope.observed_from is not None:
        stmt = stmt.where(BannerObservation.observed_at >= scope.observed_from)
    if scope.observed_to is not None:
        stmt = stmt.where(BannerObservation.observed_at <= scope.observed_to)
    stmt = stmt.order_by(BannerObservation.observed_at.asc(), BannerObservation.id.asc())
    return session.scalars(stmt).all()


def banner_share_by_brand(
    session: Session,
    *,
    scope: BannerShareScope | None = None,
) -> BannerShareSnapshot:
    """Compute Banner Share by brand for homepage observations in scope."""
    scope = scope or BannerShareScope()
    cfg = load_banner_config()
    include_unknown = bool(cfg.get("include_unknown_in_banner_share", False))
    tracked = set(cfg.get("tracked_brands") or TRACKED_BRANDS)
    unknown_codes = set(cfg.get("unknown_brand_codes") or [UNKNOWN, AMBIGUOUS])

    rows = load_banner_observations(session, scope=scope)
    total_obs = len(rows)

    tracked_rows = [
        r
        for r in rows
        if (r.brand_detected in tracked)
        or (r.is_tracked_brand and r.brand_detected not in unknown_codes)
    ]
    if include_unknown:
        denom_rows = list(rows)
    else:
        denom_rows = tracked_rows

    total_tracked = len(denom_rows)
    unknown_count = sum(
        1 for r in rows if (r.brand_detected or UNKNOWN) in unknown_codes
    )

    counts: dict[str, int] = {}
    for r in denom_rows if include_unknown else tracked_rows:
        brand = r.brand_detected or UNKNOWN
        if brand in unknown_codes and not include_unknown:
            continue
        counts[brand] = counts.get(brand, 0) + 1

    # Zero-fill tracked brands only when a tracked-brand denominator exists.
    # 0/0 must not become Intel/AMD percentages.
    if total_tracked > 0:
        for brand in TRACKED_BRANDS:
            counts.setdefault(brand, 0)

    shares = [
        BannerShareRow(
            brand=brand,
            banner_count=count,
            total_tracked_banners=total_tracked,
            banner_share=_share(count, total_tracked),
            retailer_code=scope.retailer_code,
        )
        for brand, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if brand in tracked or (include_unknown and brand in unknown_codes)
    ]

    return BannerShareSnapshot(
        scope=scope,
        total_observations=total_obs,
        total_tracked_banners=total_tracked,
        unknown_or_ambiguous=unknown_count,
        shares=shares,
        include_unknown_in_denominator=include_unknown,
    )


def banner_share_trends(
    session: Session,
    *,
    scope: BannerShareScope | None = None,
) -> list[BannerShareTrendPoint]:
    """Daily Banner Share trends (append-only history; never overwrites)."""
    scope = scope or BannerShareScope()
    cfg = load_banner_config()
    include_unknown = bool(cfg.get("include_unknown_in_banner_share", False))
    tracked = set(cfg.get("tracked_brands") or TRACKED_BRANDS)
    unknown_codes = set(cfg.get("unknown_brand_codes") or [UNKNOWN, AMBIGUOUS])

    rows = load_banner_observations(session, scope=scope)
    if not rows:
        return []

    # Group by (day, retailer)
    buckets: dict[tuple[datetime, str], list[BannerObservation]] = {}
    for row in rows:
        key = (_day_start(row.observed_at), row.retailer_code)
        buckets.setdefault(key, []).append(row)

    points: list[BannerShareTrendPoint] = []
    for (day, retailer), bucket in sorted(buckets.items()):
        if include_unknown:
            denom = bucket
        else:
            denom = [
                r
                for r in bucket
                if r.brand_detected in tracked
                or (r.is_tracked_brand and (r.brand_detected or "") not in unknown_codes)
            ]
        total = len(denom)
        counts: dict[str, int] = {b: 0 for b in TRACKED_BRANDS}
        for r in denom:
            brand = r.brand_detected or UNKNOWN
            if brand in unknown_codes and not include_unknown:
                continue
            if brand in counts or include_unknown:
                counts[brand] = counts.get(brand, 0) + 1
        for brand, count in sorted(counts.items()):
            if brand not in tracked and not include_unknown:
                continue
            points.append(
                BannerShareTrendPoint(
                    period_start=day,
                    retailer_code=retailer,
                    brand=brand,
                    banner_count=count,
                    total_tracked_banners=total,
                    banner_share=_share(count, total),
                )
            )
    return points
