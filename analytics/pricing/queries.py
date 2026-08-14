"""SQLAlchemy / SQL pricing analytics over real database tables.

Reads append-only ``price_history``, ``product_snapshots``, ``promotions``, and
``products``. Never updates historical rows.

Current (cross-sectional) KPIs use eligible computing products from the latest
catalog/pricing collection batch per retailer/country. Historical time series
use all matching ``price_history`` observations. Search observations are not a
pricing fact table.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import mean, median
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from analytics.pricing.models import (
    DimensionPriceSummary,
    PriceObservation,
    PriceTimePoint,
    PricingScope,
)
from database.models import PriceHistory, Product, ProductSnapshot, Promotion

# ---------------------------------------------------------------------------
# SQL fragments (documented for reproducibility / Postgres clients)
# ---------------------------------------------------------------------------

SQL_LATEST_PRICE_PER_PRODUCT = """
-- Latest price_history row per product (within optional filters applied in Python/ORM)
SELECT DISTINCT ON (ph.product_id)
    ph.id,
    ph.product_id,
    ph.observed_at,
    ph.price_amount,
    ph.list_price,
    ph.discount_pct,
    ph.is_on_promotion,
    ph.currency,
    p.brand,
    p.retailer_code,
    p.country_code,
    p.product_type
FROM price_history ph
JOIN products p ON p.id = ph.product_id
ORDER BY ph.product_id, ph.observed_at DESC, ph.id DESC
"""

SQL_PRICE_CHANGE_BY_DAY = """
SELECT date_trunc('day', ph.observed_at AT TIME ZONE 'UTC') AS period_start,
       ph.currency,
       COUNT(*) AS observation_count,
       AVG(ph.price_amount) AS average_price,
       AVG(ph.discount_pct) AS average_discount_pct,
       COUNT(*) FILTER (WHERE ph.is_on_promotion OR COALESCE(ph.discount_pct, 0) > 0)
           AS discounted_observation_count
FROM price_history ph
JOIN products p ON p.id = ph.product_id
GROUP BY 1, 2
ORDER BY 1, 2
"""


def _as_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _avg(values: Iterable[Decimal]) -> Optional[Decimal]:
    items = list(values)
    if not items:
        return None
    return Decimal(str(mean(items)))


def _median(values: Iterable[Decimal]) -> Optional[Decimal]:
    items = list(values)
    if not items:
        return None
    return Decimal(str(median(items)))


def _scope_product_filters(scope: PricingScope | None) -> list[Any]:
    scope = scope or PricingScope()
    filters: list[Any] = []
    if scope.brand is not None:
        filters.append(Product.brand == scope.brand)
    if scope.retailer_code is not None:
        filters.append(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        filters.append(Product.country_code == scope.country_code)
    if scope.product_type is not None:
        filters.append(Product.product_type == scope.product_type)
    return filters


def _scope_price_filters(scope: PricingScope | None) -> list[Any]:
    scope = scope or PricingScope()
    filters: list[Any] = []
    if scope.currency is not None:
        filters.append(PriceHistory.currency == scope.currency)
    if scope.observed_from is not None:
        filters.append(PriceHistory.observed_at >= scope.observed_from)
    if scope.observed_to is not None:
        filters.append(PriceHistory.observed_at <= scope.observed_to)
    return filters


def _apply_current_universe(scope: PricingScope | None, *, latest_only: bool) -> bool:
    scope = scope or PricingScope()
    return bool(latest_only and scope.current_universe)


def _product_is_pricing_eligible(product: Product) -> bool:
    """Eligible computing types via the shared classifier. Brands are not a gate."""
    from collector.retailers.mercadolibre.classification import (
        EXCLUDED,
        OTHER_TYPE,
        SUPPORTED_PRODUCT_TYPES,
        classify_mercadolibre_product,
        is_collection_eligible,
    )

    stored = (product.product_type or "").strip().lower()
    if stored == OTHER_TYPE:
        return False
    classified = classify_mercadolibre_product(
        title=product.title, category_raw=product.category_raw
    )
    if classified.status == EXCLUDED or classified.hard_negative:
        return False
    if classified.product_type == OTHER_TYPE:
        return False
    if stored in SUPPORTED_PRODUCT_TYPES:
        return True
    return is_collection_eligible(classified)


def _latest_batch_product_ids(
    session: Session, scope: PricingScope | None
) -> set[int]:
    """Products priced in the latest collection_run per retailer/country."""
    filters = _scope_product_filters(scope) + _scope_price_filters(scope)
    ranked = (
        select(
            Product.retailer_code.label("retailer_code"),
            Product.country_code.label("country_code"),
            PriceHistory.collection_run_id.label("run_id"),
            func.row_number()
            .over(
                partition_by=(Product.retailer_code, Product.country_code),
                order_by=(PriceHistory.observed_at.desc(), PriceHistory.id.desc()),
            )
            .label("rn"),
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .where(PriceHistory.collection_run_id.is_not(None))
        .where(and_(*filters) if filters else True)
    ).subquery("latest_price_run")
    run_rows = session.execute(
        select(
            ranked.c.retailer_code,
            ranked.c.country_code,
            ranked.c.run_id,
        ).where(ranked.c.rn == 1)
    ).all()
    if not run_rows:
        return set()
    product_ids: set[int] = set()
    for retailer, country, run_id in run_rows:
        stmt = (
            select(PriceHistory.product_id)
            .join(Product, Product.id == PriceHistory.product_id)
            .where(PriceHistory.collection_run_id == int(run_id))
            .where(Product.retailer_code == retailer)
            .where(Product.country_code == country)
        )
        extra = _scope_product_filters(scope) + _scope_price_filters(scope)
        if extra:
            stmt = stmt.where(and_(*extra))
        product_ids.update(int(pid) for pid in session.scalars(stmt).all())
    return product_ids


def _latest_price_ids_stmt(scope: PricingScope | None = None) -> Select[Any]:
    """IDs of the latest price_history row per product under scope filters."""
    filters = _scope_product_filters(scope) + _scope_price_filters(scope)
    ranked = (
        select(
            PriceHistory.id.label("price_id"),
            func.row_number()
            .over(
                partition_by=PriceHistory.product_id,
                order_by=(PriceHistory.observed_at.desc(), PriceHistory.id.desc()),
            )
            .label("rn"),
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .where(and_(*filters) if filters else True)
    ).subquery("ranked_prices")
    return select(ranked.c.price_id).where(ranked.c.rn == 1)


def list_price_observations(
    session: Session,
    *,
    scope: PricingScope | None = None,
    latest_only: bool = True,
) -> list[PriceObservation]:
    """Load priced observations joined to product dimensions.

    When ``latest_only`` is True (default), returns one row per product — the
    newest ``price_history`` observation in the current eligible catalog batch.
    Promotion text is shown only when that latest price row is on promotion.
    """
    scope = scope or PricingScope()
    filters = _scope_product_filters(scope) + _scope_price_filters(scope)
    stmt = (
        select(PriceHistory, Product)
        .join(Product, Product.id == PriceHistory.product_id)
        .where(and_(*filters) if filters else True)
        .order_by(PriceHistory.observed_at.asc(), PriceHistory.id.asc())
    )
    if latest_only:
        latest_ids = _latest_price_ids_stmt(scope)
        stmt = stmt.where(PriceHistory.id.in_(latest_ids))

    rows = session.execute(stmt).all()
    if _apply_current_universe(scope, latest_only=latest_only):
        batch_ids = _latest_batch_product_ids(session, scope)
        rows = [
            (price, product)
            for price, product in rows
            if int(product.id) in batch_ids and _product_is_pricing_eligible(product)
        ]

    product_ids = {product.id for _, product in rows}
    promo_by_product = _latest_promo_texts(session, product_ids)

    out: list[PriceObservation] = []
    for price, product in rows:
        on_promo = bool(price.is_on_promotion)
        out.append(
            PriceObservation(
                product_id=product.id,
                brand=product.brand,
                retailer_code=product.retailer_code,
                country_code=product.country_code,
                product_type=product.product_type,
                currency=price.currency,
                current_price=_as_decimal(price.price_amount),
                original_price=_as_decimal(price.list_price),
                discount_pct=_as_decimal(price.discount_pct),
                promotion_text=promo_by_product.get(product.id) if on_promo else None,
                is_on_promotion=on_promo,
                observed_at=price.observed_at,
                source="price_history",
            )
        )
    return out


def list_snapshot_pricing_rows(
    session: Session,
    *,
    scope: PricingScope | None = None,
    latest_only: bool = True,
) -> list[PriceObservation]:
    """Read pricing fields denormalized onto ``product_snapshots``.

    Requires migration ``0002_snapshot_pricing_fields``. Snapshots without
    ``currency`` + ``price_amount`` are skipped.
    """
    scope = scope or PricingScope()
    filters = _scope_product_filters(scope)
    if scope.currency is not None:
        filters.append(ProductSnapshot.currency == scope.currency)
    if scope.observed_from is not None:
        filters.append(ProductSnapshot.observed_at >= scope.observed_from)
    if scope.observed_to is not None:
        filters.append(ProductSnapshot.observed_at <= scope.observed_to)

    stmt = (
        select(ProductSnapshot, Product)
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(and_(*filters) if filters else True)
        .where(ProductSnapshot.price_amount.is_not(None))
        .where(ProductSnapshot.currency.is_not(None))
        .order_by(ProductSnapshot.observed_at.asc(), ProductSnapshot.id.asc())
    )
    if latest_only:
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
            .where(and_(*filters) if filters else True)
        ).subquery("ranked_snaps")
        stmt = stmt.where(
            ProductSnapshot.id.in_(select(ranked.c.snap_id).where(ranked.c.rn == 1))
        )

    pairs = list(session.execute(stmt).all())
    if _apply_current_universe(scope, latest_only=latest_only):
        batch_ids = _latest_batch_product_ids(session, scope)
        pairs = [
            (snap, product)
            for snap, product in pairs
            if int(product.id) in batch_ids and _product_is_pricing_eligible(product)
        ]

    out: list[PriceObservation] = []
    for snap, product in pairs:
        on_promo = bool(snap.is_on_promotion)
        out.append(
            PriceObservation(
                product_id=product.id,
                brand=snap.brand or product.brand,
                retailer_code=product.retailer_code,
                country_code=product.country_code,
                product_type=snap.product_type or product.product_type,
                currency=str(snap.currency),
                current_price=_as_decimal(snap.price_amount),
                original_price=_as_decimal(snap.list_price),
                discount_pct=_as_decimal(snap.discount_pct),
                promotion_text=snap.promo_text if on_promo else None,
                is_on_promotion=on_promo,
                observed_at=snap.observed_at,
                source="product_snapshots",
            )
        )
    return out


def _latest_promo_texts(
    session: Session, product_ids: set[int]
) -> dict[int, Optional[str]]:
    if not product_ids:
        return {}
    ranked = (
        select(
            Promotion.product_id,
            Promotion.promo_text,
            func.row_number()
            .over(
                partition_by=Promotion.product_id,
                order_by=(Promotion.observed_at.desc(), Promotion.id.desc()),
            )
            .label("rn"),
        )
        .where(Promotion.product_id.in_(product_ids))
        .where(Promotion.promo_text.is_not(None))
    ).subquery("ranked_promos")
    rows = session.execute(
        select(ranked.c.product_id, ranked.c.promo_text).where(ranked.c.rn == 1)
    ).all()
    return {int(pid): text for pid, text in rows}


def _is_discounted(obs: PriceObservation) -> bool:
    if obs.is_on_promotion:
        return True
    if obs.discount_pct is not None and obs.discount_pct > 0:
        return True
    if (
        obs.original_price is not None
        and obs.current_price is not None
        and obs.original_price > obs.current_price
    ):
        return True
    return False


def _summarize(
    observations: Sequence[PriceObservation],
    *,
    dimension: str,
    value: str,
    currency: str,
) -> DimensionPriceSummary:
    priced = [o for o in observations if o.current_price is not None]
    prices = [o.current_price for o in priced if o.current_price is not None]
    discounts = [
        o.discount_pct for o in observations if o.discount_pct is not None
    ]
    discounted_ids = {o.product_id for o in observations if _is_discounted(o)}
    return DimensionPriceSummary(
        dimension=dimension,
        value=value,
        currency=currency,
        product_count=len({o.product_id for o in observations}),
        observation_count=len(observations),
        average_price=_avg(prices),
        median_price=_median(prices),
        average_discount_pct=_avg(discounts),
        discounted_product_count=len(discounted_ids),
    )


def _group_summaries(
    observations: Sequence[PriceObservation],
    *,
    dimension: str,
    key_fn,
) -> list[DimensionPriceSummary]:
    buckets: dict[tuple[str, str], list[PriceObservation]] = {}
    for obs in observations:
        key = key_fn(obs)
        if key is None:
            continue
        value, currency = key
        buckets.setdefault((value, currency), []).append(obs)

    summaries = [
        _summarize(rows, dimension=dimension, value=value, currency=currency)
        for (value, currency), rows in buckets.items()
    ]
    summaries.sort(key=lambda s: (s.currency, s.value))
    return summaries


def average_price_by_brand(
    session: Session, *, scope: PricingScope | None = None
) -> list[DimensionPriceSummary]:
    """Average (and median) latest price per brand within scope."""
    return _group_summaries(
        list_price_observations(session, scope=scope, latest_only=True),
        dimension="brand",
        key_fn=lambda o: (o.brand, o.currency) if o.brand else None,
    )


def median_price_by_brand(
    session: Session, *, scope: PricingScope | None = None
) -> dict[tuple[str, str], Decimal]:
    """Median latest price keyed by (brand, currency)."""
    return {
        (row.value, row.currency): row.median_price
        for row in average_price_by_brand(session, scope=scope)
        if row.median_price is not None
    }


def average_discount(
    session: Session, *, scope: PricingScope | None = None
) -> Optional[Decimal]:
    """Mean discount_pct across latest priced products that have a discount_pct."""
    obs = list_price_observations(session, scope=scope, latest_only=True)
    values = [o.discount_pct for o in obs if o.discount_pct is not None]
    return _avg(values)


def count_discounted_products(
    session: Session, *, scope: PricingScope | None = None
) -> int:
    """Count distinct products whose latest observation is discounted / on promo."""
    obs = list_price_observations(session, scope=scope, latest_only=True)
    return len({o.product_id for o in obs if _is_discounted(o)})


def compare_by_retailer(
    session: Session, *, scope: PricingScope | None = None
) -> list[DimensionPriceSummary]:
    return _group_summaries(
        list_price_observations(session, scope=scope, latest_only=True),
        dimension="retailer_code",
        key_fn=lambda o: (o.retailer_code, o.currency),
    )


def compare_by_country(
    session: Session, *, scope: PricingScope | None = None
) -> list[DimensionPriceSummary]:
    return _group_summaries(
        list_price_observations(session, scope=scope, latest_only=True),
        dimension="country_code",
        key_fn=lambda o: (o.country_code, o.currency),
    )


def compare_by_product_type(
    session: Session, *, scope: PricingScope | None = None
) -> list[DimensionPriceSummary]:
    return _group_summaries(
        list_price_observations(session, scope=scope, latest_only=True),
        dimension="product_type",
        key_fn=lambda o: (o.product_type, o.currency) if o.product_type else None,
    )


def _day_bucket(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(value.tzinfo).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def price_change_over_time(
    session: Session, *, scope: PricingScope | None = None
) -> list[PriceTimePoint]:
    """Daily average current price (all observations in scope, not latest-only)."""
    return _time_series(session, scope=scope)


def discount_change_over_time(
    session: Session, *, scope: PricingScope | None = None
) -> list[PriceTimePoint]:
    """Daily average discount_pct (same buckets as price_change_over_time)."""
    return _time_series(session, scope=scope)


def _time_series(
    session: Session, *, scope: PricingScope | None
) -> list[PriceTimePoint]:
    observations = list_price_observations(session, scope=scope, latest_only=False)
    buckets: dict[tuple[datetime, str], list[PriceObservation]] = {}
    for obs in observations:
        key = (_day_bucket(obs.observed_at), obs.currency)
        buckets.setdefault(key, []).append(obs)

    points: list[PriceTimePoint] = []
    for (period_start, currency), rows in sorted(buckets.items()):
        prices = [o.current_price for o in rows if o.current_price is not None]
        discounts = [o.discount_pct for o in rows if o.discount_pct is not None]
        discounted = sum(1 for o in rows if _is_discounted(o))
        points.append(
            PriceTimePoint(
                period_start=period_start,
                currency=currency,
                observation_count=len(rows),
                average_price=_avg(prices),
                average_discount_pct=_avg(discounts),
                discounted_observation_count=discounted,
            )
        )
    return points
