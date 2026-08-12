"""Share of Shelf queries over real ``products`` / ``product_snapshots`` rows."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from analytics.share_of_shelf.models import (
    SosExclusionBreakdown,
    SosScope,
    SosShare,
    SosSnapshot,
    SosTrendPoint,
)
from analytics.share_of_shelf.universe import (
    INCLUSION_RULES_ID,
    EligibleListing,
    SosUniverseConfig,
    build_eligible_universe,
    load_sos_universe_config,
)
from database.models import Product, ProductSnapshot

# Reproducible SQL sketch (Postgres) for the live products universe:
SQL_CANDIDATE_PRODUCTS = """
SELECT id AS product_id, retailer_code, country_code, retailer_sku,
       brand, oem, product_type, title, category_raw
FROM products
WHERE is_active = TRUE
"""


def _share(count: int, universe_size: int) -> Decimal:
    if universe_size <= 0:
        return Decimal("0")
    return (Decimal(count) / Decimal(universe_size)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _day_start(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _apply_scope_to_product_query(stmt: Any, scope: SosScope) -> Any:
    if scope.retailer_code is not None:
        stmt = stmt.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        stmt = stmt.where(Product.country_code == scope.country_code)
    if scope.product_type is not None:
        stmt = stmt.where(Product.product_type == scope.product_type)
    if scope.oem is not None:
        stmt = stmt.where(Product.oem == scope.oem)
    if scope.brand is not None:
        stmt = stmt.where(Product.brand == scope.brand)
    return stmt


def _load_candidates_from_products(
    session: Session, scope: SosScope
) -> tuple[list[dict[str, Any]], int]:
    """Return candidate dicts and count of rows dropped by scope/inactive only."""
    stmt = select(Product).where(Product.is_active.is_(True))
    scoped = _apply_scope_to_product_query(stmt, scope)
    # Count inactive / out-of-scope separately for diagnostics
    total_active = session.scalar(
        select(func.count()).select_from(Product).where(Product.is_active.is_(True))
    ) or 0
    rows = session.scalars(scoped).all()
    scope_filtered = int(total_active) - len(rows)
    candidates = [
        {
            "product_id": p.id,
            "retailer_code": p.retailer_code,
            "country_code": p.country_code,
            "retailer_sku": p.retailer_sku,
            "brand": p.brand,
            "oem": p.oem,
            "product_type": p.product_type,
            "title": p.title,
            "category_raw": p.category_raw,
            "availability": None,
        }
        for p in rows
    ]
    return candidates, max(scope_filtered, 0)


def _load_candidates_as_of(
    session: Session, scope: SosScope
) -> tuple[list[dict[str, Any]], int]:
    """Latest snapshot per product at or before ``scope.as_of``."""
    assert scope.as_of is not None
    as_of = scope.as_of
    ranked = (
        select(
            ProductSnapshot.id.label("snap_id"),
            func.row_number()
            .over(
                partition_by=ProductSnapshot.product_id,
                order_by=(
                    ProductSnapshot.observed_at.desc(),
                    ProductSnapshot.id.desc(),
                ),
            )
            .label("rn"),
        )
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(ProductSnapshot.observed_at <= as_of)
    )
    if scope.retailer_code is not None:
        ranked = ranked.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        ranked = ranked.where(Product.country_code == scope.country_code)
    ranked_sq = ranked.subquery("ranked_snaps")

    stmt = (
        select(ProductSnapshot, Product)
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(
            ProductSnapshot.id.in_(
                select(ranked_sq.c.snap_id).where(ranked_sq.c.rn == 1)
            )
        )
    )
    if scope.product_type is not None:
        stmt = stmt.where(
            func.coalesce(ProductSnapshot.product_type, Product.product_type)
            == scope.product_type
        )
    if scope.oem is not None:
        stmt = stmt.where(func.coalesce(ProductSnapshot.oem, Product.oem) == scope.oem)
    if scope.brand is not None:
        stmt = stmt.where(
            func.coalesce(ProductSnapshot.brand, Product.brand) == scope.brand
        )

    candidates: list[dict[str, Any]] = []
    for snap, product in session.execute(stmt).all():
        candidates.append(
            {
                "product_id": product.id,
                "retailer_code": product.retailer_code,
                "country_code": product.country_code,
                "retailer_sku": product.retailer_sku,
                "brand": snap.brand or product.brand,
                "oem": snap.oem or product.oem,
                "product_type": snap.product_type or product.product_type,
                "title": snap.title or product.title,
                "category_raw": snap.category_raw or product.category_raw,
                "availability": snap.availability,
            }
        )
    return candidates, 0


def load_eligible_listings(
    session: Session,
    *,
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> tuple[list[EligibleListing], SosExclusionBreakdown]:
    """Build the eligible SoS universe for the given scope from the database."""
    scope = scope or SosScope()
    cfg = config or load_sos_universe_config()
    if scope.as_of is not None:
        candidates, scope_filtered = _load_candidates_as_of(session, scope)
    else:
        candidates, scope_filtered = _load_candidates_from_products(session, scope)

    eligible, raw_exclusions = build_eligible_universe(candidates, config=cfg)
    exclusions = SosExclusionBreakdown(
        accessory_or_ineligible_type=raw_exclusions.get(
            "accessory_or_ineligible_type", 0
        ),
        non_gaming=raw_exclusions.get("non_gaming", 0),
        missing_identity=raw_exclusions.get("missing_identity", 0)
        + raw_exclusions.get("duplicate_sku", 0),
        scope_filtered=scope_filtered,
        inactive=0,
    )
    return eligible, exclusions


def _aggregate_shares(
    listings: Sequence[EligibleListing],
    *,
    dimension: str,
) -> list[SosShare]:
    """Aggregate SoS by brand or oem. Each listing counted once."""
    if dimension not in {"brand", "oem"}:
        raise ValueError("dimension must be 'brand' or 'oem'")

    universe_size = len(listings)
    counts: dict[str, int] = {}
    for item in listings:
        raw = item.brand if dimension == "brand" else item.oem
        if raw is None or str(raw).strip() == "":
            key = "UNKNOWN"
        else:
            key = str(raw)
        # Brand path never consults oem — prevents Apple Brand+OEM double count.
        counts[key] = counts.get(key, 0) + 1

    shares = [
        SosShare(
            dimension=dimension,
            value=value,
            product_count=count,
            universe_size=universe_size,
            share=_share(count, universe_size),
        )
        for value, count in counts.items()
    ]
    shares.sort(key=lambda s: (-s.product_count, s.value))
    return shares


def share_of_shelf(
    session: Session,
    *,
    dimension: str = "brand",
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> SosSnapshot:
    """Compute Share of Shelf for ``brand`` or ``oem`` over the eligible universe."""
    scope = scope or SosScope()
    cfg = config or load_sos_universe_config()
    listings, exclusions = load_eligible_listings(session, scope=scope, config=cfg)
    shares = _aggregate_shares(listings, dimension=dimension)
    return SosSnapshot(
        scope=scope,
        dimension=dimension,
        universe_size=len(listings),
        inclusion_rules_id=cfg.inclusion_rules_id or INCLUSION_RULES_ID,
        shares=shares,
        exclusions=exclusions,
        as_of=scope.as_of,
    )


def share_of_shelf_by_brand(
    session: Session,
    *,
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> SosSnapshot:
    """Brand SoS: each product attributed once via ``brand`` (Apple counted once)."""
    return share_of_shelf(session, dimension="brand", scope=scope, config=config)


def share_of_shelf_by_oem(
    session: Session,
    *,
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> SosSnapshot:
    """OEM drilldown over the same eligible universe (separate from brand SoS)."""
    return share_of_shelf(session, dimension="oem", scope=scope, config=config)


def share_of_shelf_trends(
    session: Session,
    *,
    dimension: str = "brand",
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> list[SosTrendPoint]:
    """Historical SoS by UTC day using latest snapshot-as-of each day end.

    For each distinct snapshot calendar day in scope, builds the eligible
    universe as-of 23:59:59.999999 UTC that day and computes shares.
    """
    scope = scope or SosScope()
    cfg = config or load_sos_universe_config()

    day_stmt = select(ProductSnapshot.observed_at).join(
        Product, Product.id == ProductSnapshot.product_id
    )
    if scope.retailer_code is not None:
        day_stmt = day_stmt.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        day_stmt = day_stmt.where(Product.country_code == scope.country_code)
    timestamps = session.scalars(day_stmt).all()
    if not timestamps:
        return []

    days = sorted({_day_start(ts) for ts in timestamps})
    points: list[SosTrendPoint] = []
    for day in days:
        # End of UTC day
        as_of = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        day_scope = SosScope(
            retailer_code=scope.retailer_code,
            country_code=scope.country_code,
            product_type=scope.product_type,
            oem=scope.oem,
            brand=scope.brand,
            as_of=as_of,
        )
        snap = share_of_shelf(
            session, dimension=dimension, scope=day_scope, config=cfg
        )
        points.append(
            SosTrendPoint(
                period_start=day,
                universe_size=snap.universe_size,
                shares=tuple(snap.shares),
            )
        )
    return points
