"""Share of Voice queries over append-only ``search_observations``."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.share_of_voice.models import (
    BrandKeywordMetrics,
    SovScope,
    SovSnapshot,
    SovTrendPoint,
)
from collector.search.config import load_sov_config
from database.models import SearchObservation

TRACKED_DEFAULT = ("Intel", "AMD", "Qualcomm", "Apple")
UNKNOWN = "UNKNOWN"


def _share(count: int, total: int) -> Decimal:
    if total <= 0:
        return Decimal("0")
    return (Decimal(count) / Decimal(total)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _avg(values: Iterable[int]) -> Optional[Decimal]:
    items = list(values)
    if not items:
        return None
    return (Decimal(sum(items)) / Decimal(len(items))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _day_start(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _load_rows(session: Session, scope: SovScope) -> Sequence[SearchObservation]:
    cfg = load_sov_config()
    stmt = select(SearchObservation)
    if scope.retailer_code is not None:
        stmt = stmt.where(SearchObservation.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        stmt = stmt.where(SearchObservation.country_code == scope.country_code)
    if scope.keyword is not None:
        stmt = stmt.where(SearchObservation.keyword == scope.keyword)
    if scope.observed_from is not None:
        stmt = stmt.where(SearchObservation.observed_at >= scope.observed_from)
    if scope.observed_to is not None:
        stmt = stmt.where(SearchObservation.observed_at <= scope.observed_to)
    if scope.require_complete:
        stmt = stmt.where(SearchObservation.collection_status == "COMPLETE")
    organic_only = (
        cfg.prefer_organic if scope.organic_only is None else scope.organic_only
    )
    if organic_only:
        stmt = stmt.where(SearchObservation.is_sponsored.is_(False))
    stmt = stmt.order_by(
        SearchObservation.observed_at.asc(),
        SearchObservation.position.asc(),
        SearchObservation.id.asc(),
    )
    return session.scalars(stmt).all()


def _observation_is_eligible(row: SearchObservation) -> bool:
    """Historical junk can be flagged ineligible while preserving the row."""
    details = row.details if isinstance(row.details, dict) else {}
    if "is_eligible" in details:
        return bool(details.get("is_eligible"))
    # Backfill gate for Mercado Libre historical pollution (do not delete rows).
    if row.retailer_code == "mercadolibre":
        from collector.retailers.mercadolibre.classification import (
            classify_mercadolibre_product,
            is_collection_eligible,
        )

        return is_collection_eligible(classify_mercadolibre_product(title=row.title))
    return True


def _dedupe_latest_search(
    rows: Sequence[SearchObservation],
) -> list[SearchObservation]:
    """Keep one observation per (retailer, country, keyword, position) from the
    latest search batch (by observed_at), avoiding join/double-count artifacts.

    Ineligible historical hits (e.g. ML TVs in search) are excluded from SoV
    denominators while remaining stored append-only.
    """
    if not rows:
        return []
    # Latest observed_at per keyword scope
    latest: dict[tuple[str, str, str], datetime] = {}
    for r in rows:
        key = (r.retailer_code, r.country_code, r.keyword)
        prev = latest.get(key)
        if prev is None or r.observed_at > prev:
            latest[key] = r.observed_at

    filtered = [
        r
        for r in rows
        if r.observed_at == latest[(r.retailer_code, r.country_code, r.keyword)]
        and _observation_is_eligible(r)
    ]
    # Dedupe identical positions within the batch (keep first id)
    seen: set[tuple[str, str, str, int]] = set()
    out: list[SearchObservation] = []
    for r in filtered:
        slot = (r.retailer_code, r.country_code, r.keyword, int(r.position))
        if slot in seen:
            continue
        seen.add(slot)
        out.append(r)
    return out


def _basis_for_rows(rows: Sequence[SearchObservation]) -> str:
    statuses = {r.collection_status for r in rows}
    if not statuses:
        return "empty"
    if statuses == {"COMPLETE"}:
        return "exact"
    if "PARTIAL" in statuses or "FAILED" in statuses:
        if "COMPLETE" in statuses:
            return "mixed"
        return "observed_partial"
    if statuses == {"ZERO_RESULTS"}:
        return "empty"
    return "mixed"


def brand_presence(
    session: Session,
    *,
    scope: SovScope | None = None,
) -> dict[str, bool]:
    scope = scope or SovScope()
    cfg = load_sov_config()
    rows = _dedupe_latest_search(_load_rows(session, scope))
    present = {b: False for b in cfg.tracked_brands}
    for r in rows:
        if r.brand in present:
            present[r.brand] = True
    return present


def keyword_metrics(
    session: Session,
    *,
    scope: SovScope | None = None,
    top_n: int | None = None,
) -> list[BrandKeywordMetrics]:
    """Per-brand metrics for a scoped result set (optionally one keyword)."""
    scope = scope or SovScope()
    cfg = load_sov_config()
    n = int(top_n or scope.top_n or cfg.default_top_n)
    rows = _dedupe_latest_search(_load_rows(session, scope))
    basis = _basis_for_rows(rows)

    tracked = set(cfg.tracked_brands)
    include_unknown = cfg.include_unknown_in_denominator

    def is_denom(brand: Optional[str]) -> bool:
        if brand in tracked:
            return True
        return bool(include_unknown and (brand is None or brand == UNKNOWN))

    denom_rows = [r for r in rows if is_denom(r.brand)]
    total_tracked = len(denom_rows)

    by_brand: dict[str, list[SearchObservation]] = defaultdict(list)
    for r in rows:
        brand = r.brand or UNKNOWN
        if brand in tracked or brand == UNKNOWN:
            by_brand[brand].append(r)

    metrics: list[BrandKeywordMetrics] = []
    for brand in list(cfg.tracked_brands) + ([UNKNOWN] if UNKNOWN in by_brand else []):
        brand_rows = by_brand.get(brand, [])
        if brand == UNKNOWN and not include_unknown:
            # Still report presence/appearances but SoV share = 0 vs tracked denom
            appearances = len(brand_rows)
            top_count = sum(1 for r in brand_rows if int(r.position) <= n)
            ranks = [int(r.position) for r in brand_rows]
            metrics.append(
                BrandKeywordMetrics(
                    brand=brand,
                    keyword=scope.keyword,
                    retailer_code=scope.retailer_code,
                    country_code=scope.country_code,
                    present=appearances > 0,
                    appearances=appearances,
                    top_n_count=top_count,
                    top_n=n,
                    average_rank=_avg(ranks),
                    rank_observation_count=len(ranks),
                    share_of_voice=Decimal("0"),
                    total_tracked_appearances=total_tracked,
                    collection_basis=basis,
                )
            )
            continue

        appearances = len(brand_rows)
        top_count = sum(1 for r in brand_rows if int(r.position) <= n)
        ranks = [int(r.position) for r in brand_rows]
        metrics.append(
            BrandKeywordMetrics(
                brand=brand,
                keyword=scope.keyword,
                retailer_code=scope.retailer_code,
                country_code=scope.country_code,
                present=appearances > 0,
                appearances=appearances,
                top_n_count=top_count,
                top_n=n,
                average_rank=_avg(ranks),
                rank_observation_count=len(ranks),
                share_of_voice=_share(appearances, total_tracked)
                if brand in tracked or include_unknown
                else Decimal("0"),
                total_tracked_appearances=total_tracked,
                collection_basis=basis,
            )
        )
    return metrics


def share_of_voice(
    session: Session,
    *,
    scope: SovScope | None = None,
    top_n: int | None = None,
) -> SovSnapshot:
    scope = scope or SovScope()
    cfg = load_sov_config()
    rows = _dedupe_latest_search(_load_rows(session, scope))
    metrics = keyword_metrics(session, scope=scope, top_n=top_n)

    # Search completeness counts by unique keyword runs in latest batch
    run_keys: dict[tuple[str, str, str], str] = {}
    for r in rows:
        key = (r.retailer_code, r.country_code, r.keyword)
        run_keys[key] = r.collection_status

    complete = sum(1 for s in run_keys.values() if s == "COMPLETE")
    partial = sum(1 for s in run_keys.values() if s == "PARTIAL")
    failed = sum(1 for s in run_keys.values() if s == "FAILED")

    unknown = sum(1 for r in rows if (r.brand or UNKNOWN) == UNKNOWN)
    tracked = sum(1 for r in rows if r.brand in set(cfg.tracked_brands))

    return SovSnapshot(
        scope=scope,
        total_observations=len(rows),
        tracked_appearances=tracked,
        unknown_appearances=unknown,
        complete_searches=complete,
        partial_searches=partial,
        failed_searches=failed,
        metrics=[m for m in metrics if m.brand in set(cfg.tracked_brands)],
        collection_basis=_basis_for_rows(rows),
    )


def share_of_voice_trends(
    session: Session,
    *,
    scope: SovScope | None = None,
    top_n: int | None = None,
) -> list[SovTrendPoint]:
    scope = scope or SovScope()
    cfg = load_sov_config()
    n = int(top_n or scope.top_n or cfg.default_top_n)
    # Do not dedupe to latest only — trends need all days
    rows = list(_load_rows(session, scope))
    if not rows:
        return []

    buckets: dict[tuple[datetime, str, str, str], list[SearchObservation]] = defaultdict(
        list
    )
    for r in rows:
        key = (
            _day_start(r.observed_at),
            r.retailer_code,
            r.country_code,
            r.keyword,
        )
        buckets[key].append(r)

    points: list[SovTrendPoint] = []
    tracked = set(cfg.tracked_brands)
    for (day, retailer, country, keyword), bucket in sorted(buckets.items()):
        # Dedupe positions within the day/keyword
        seen: set[int] = set()
        unique: list[SearchObservation] = []
        for r in sorted(bucket, key=lambda x: (x.position, x.id)):
            if r.position in seen:
                continue
            seen.add(int(r.position))
            unique.append(r)
        denom = [
            r
            for r in unique
            if r.brand in tracked
            or (
                cfg.include_unknown_in_denominator
                and (r.brand is None or r.brand == UNKNOWN)
            )
        ]
        total = len(denom)
        for brand in cfg.tracked_brands:
            brand_rows = [r for r in unique if r.brand == brand]
            appearances = len(brand_rows)
            top_count = sum(1 for r in brand_rows if int(r.position) <= n)
            ranks = [int(r.position) for r in brand_rows]
            points.append(
                SovTrendPoint(
                    period_start=day,
                    retailer_code=retailer,
                    country_code=country,
                    keyword=keyword,
                    brand=brand,
                    appearances=appearances,
                    top_n_count=top_count,
                    average_rank=_avg(ranks),
                    share_of_voice=_share(appearances, total),
                    total_tracked_appearances=total,
                )
            )
    return points
