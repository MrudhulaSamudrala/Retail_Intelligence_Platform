"""Product visibility queries over ``search_observations``.

Retailer-specific visibility never mixes retailers.
Cross-retailer visibility only uses MATCHED canonical pairs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.product_identity.config import load_product_identity_config
from analytics.product_identity.matching import MATCHED
from analytics.product_identity.queries import product_availability_matrix
from analytics.product_visibility.models import (
    CrossRetailerVisibilityRow,
    ProductVisibilityRow,
    VisibilityScope,
)
from analytics.share_of_voice.queries import _dedupe_latest_search
from database.models import Product, ProductCrosswalk, SearchObservation


def _q(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _avg(values: list[int]) -> Optional[Decimal]:
    if not values:
        return None
    return (Decimal(sum(values)) / Decimal(len(values))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _load_search_rows(session: Session, scope: VisibilityScope) -> Sequence[SearchObservation]:
    cfg = load_product_identity_config()
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
    organic = cfg.prefer_organic if scope.organic_only is None else scope.organic_only
    if organic:
        stmt = stmt.where(SearchObservation.is_sponsored.is_(False))
    stmt = stmt.order_by(
        SearchObservation.observed_at.asc(),
        SearchObservation.position.asc(),
        SearchObservation.id.asc(),
    )
    rows = session.scalars(stmt).all()
    if cfg.use_latest_batch_only:
        rows = _dedupe_latest_search(rows)
    return rows


def _sku_to_product_map(session: Session) -> dict[tuple[str, str, str], Product]:
    products = session.scalars(select(Product).where(Product.is_active.is_(True))).all()
    out: dict[tuple[str, str, str], Product] = {}
    for p in products:
        key = (p.retailer_code, p.country_code, (p.retailer_sku or "").upper())
        out[key] = p
    return out


def _resolve_product(
    row: SearchObservation,
    sku_map: dict[tuple[str, str, str], Product],
) -> Optional[Product]:
    if row.product_id is not None:
        # Caller may pass preloaded map; product_id path handled in aggregation
        return None
    sku = (row.retailer_sku or "").upper()
    if not sku:
        return None
    return sku_map.get((row.retailer_code, row.country_code, sku))


def _visibility_score(
    *,
    appearances: int,
    top3: int,
    top5: int,
    top10: int,
    top20: int,
    positions: list[int],
) -> Decimal:
    weights = load_product_identity_config().visibility
    inv = sum(1.0 / p for p in positions if p > 0)
    raw = (
        weights.appearances * appearances
        + weights.top3 * top3
        + weights.top5 * top5
        + weights.top10 * top10
        + weights.top20 * top20
        + weights.inverse_rank * inv
    )
    return _q(raw)


def list_product_visibility(
    session: Session,
    scope: VisibilityScope | None = None,
) -> list[ProductVisibilityRow]:
    """Retailer-scoped product visibility rows (one retailer per call when filtered)."""
    scope = scope or VisibilityScope()
    rows = _load_search_rows(session, scope)
    sku_map = _sku_to_product_map(session)

    # key = (retailer, country, product_id or sku-fallback)
    buckets: dict[tuple[str, str, str], list[SearchObservation]] = defaultdict(list)
    meta: dict[tuple[str, str, str], dict] = {}

    product_by_id = {
        int(p.id): p for p in session.scalars(select(Product)).all()
    }
    crosswalk = {
        int(c.product_id): c
        for c in session.scalars(select(ProductCrosswalk)).all()
    }

    for row in rows:
        product: Optional[Product] = None
        if row.product_id is not None:
            product = product_by_id.get(int(row.product_id))
        if product is None:
            product = _resolve_product(row, sku_map)

        if product is not None:
            key = (product.retailer_code, product.country_code, f"id:{product.id}")
            meta[key] = {
                "product_id": product.id,
                "retailer_sku": product.retailer_sku,
                "title": product.title or row.title,
                "brand": product.brand or row.brand,
                "oem": product.oem or row.oem,
                "product_type": product.product_type,
                "canonical_product_id": (
                    crosswalk[product.id].canonical_product_id
                    if product.id in crosswalk
                    else None
                ),
            }
        else:
            sku = (row.retailer_sku or "").upper() or f"title:{(row.title or '')[:40]}"
            key = (row.retailer_code, row.country_code, f"sku:{sku}")
            meta.setdefault(
                key,
                {
                    "product_id": None,
                    "retailer_sku": row.retailer_sku,
                    "title": row.title,
                    "brand": row.brand,
                    "oem": row.oem,
                    "product_type": None,
                    "canonical_product_id": None,
                },
            )

        if scope.brand and (meta[key].get("brand") or "") != scope.brand:
            continue
        if scope.product_type and (meta[key].get("product_type") or "") != scope.product_type:
            continue
        if scope.require_linked_product and meta[key].get("product_id") is None:
            continue
        buckets[key].append(row)

    results: list[ProductVisibilityRow] = []
    for key, hits in buckets.items():
        positions = [int(h.position) for h in hits]
        appearances = len(positions)
        top3 = sum(1 for p in positions if p <= 3)
        top5 = sum(1 for p in positions if p <= 5)
        top10 = sum(1 for p in positions if p <= 10)
        top20 = sum(1 for p in positions if p <= 20)
        keywords = tuple(sorted({h.keyword for h in hits}))
        info = meta[key]
        results.append(
            ProductVisibilityRow(
                retailer_code=key[0],
                country_code=key[1],
                product_id=info.get("product_id"),
                retailer_sku=info.get("retailer_sku"),
                title=info.get("title"),
                brand=info.get("brand"),
                oem=info.get("oem"),
                product_type=info.get("product_type"),
                canonical_product_id=info.get("canonical_product_id"),
                appearances=appearances,
                top3_appearances=top3,
                top5_appearances=top5,
                top10_appearances=top10,
                top20_appearances=top20,
                average_rank=_avg(positions),
                keywords=keywords,
                visibility_score=_visibility_score(
                    appearances=appearances,
                    top3=top3,
                    top5=top5,
                    top10=top10,
                    top20=top20,
                    positions=positions,
                ),
            )
        )

    results.sort(
        key=lambda r: (-r.visibility_score, -r.appearances, r.title or "")
    )
    return results[: max(scope.top_n, 1)] if scope.top_n else results


def highest_visibility_by_retailer(
    session: Session,
    retailer: str,
    *,
    country_code: str | None = None,
    keyword: str | None = None,
    brand: str | None = None,
    product_type: str | None = None,
    observed_from: datetime | None = None,
    observed_to: datetime | None = None,
    top_n: int = 1,
    require_linked_product: bool = True,
) -> list[ProductVisibilityRow]:
    """Highest product visibility within a single retailer (never cross-mixed).

    Defaults to catalog-linked products only so answers include ``product_id``.
    """
    scope = VisibilityScope(
        retailer_code=retailer,
        country_code=country_code,
        keyword=keyword,
        brand=brand,
        product_type=product_type,
        observed_from=observed_from,
        observed_to=observed_to,
        require_linked_product=require_linked_product,
        top_n=top_n,
    )
    return list_product_visibility(session, scope)


def list_cross_retailer_visibility(
    session: Session,
    *,
    top_n: int = 20,
    keyword: str | None = None,
) -> list[CrossRetailerVisibilityRow]:
    """Canonical products MATCHED on both Newegg and Mercado Libre."""
    common = [
        row
        for row in product_availability_matrix(session)
        if row.on_newegg
        and row.on_mercadolibre
        and row.match_status == MATCHED
    ]
    newegg_vis = {
        r.product_id: r
        for r in list_product_visibility(
            session,
            VisibilityScope(retailer_code="newegg", keyword=keyword, top_n=10_000),
        )
        if r.product_id is not None
    }
    ml_vis = {
        r.product_id: r
        for r in list_product_visibility(
            session,
            VisibilityScope(
                retailer_code="mercadolibre", keyword=keyword, top_n=10_000
            ),
        )
        if r.product_id is not None
    }

    out: list[CrossRetailerVisibilityRow] = []
    for row in common:
        nv = newegg_vis.get(row.newegg_product_id) if row.newegg_product_id else None
        mv = ml_vis.get(row.mercadolibre_product_id) if row.mercadolibre_product_id else None
        combined_app = (nv.appearances if nv else 0) + (mv.appearances if mv else 0)
        combined_score = _q(
            float(nv.visibility_score if nv else 0)
            + float(mv.visibility_score if mv else 0)
        )
        out.append(
            CrossRetailerVisibilityRow(
                canonical_product_id=row.canonical_product_id,
                display_name=row.display_name,
                manufacturer_model=row.manufacturer_model,
                oem=row.oem,
                match_status=row.match_status,
                match_method=row.match_method,
                match_confidence=row.match_confidence,
                newegg_product_id=row.newegg_product_id,
                mercadolibre_product_id=row.mercadolibre_product_id,
                newegg_visibility=nv,
                mercadolibre_visibility=mv,
                combined_appearances=combined_app,
                combined_visibility_score=combined_score,
            )
        )

    out.sort(
        key=lambda r: (
            -r.combined_visibility_score,
            -r.combined_appearances,
            r.display_name or "",
        )
    )
    return out[: max(top_n, 1)]


def highest_cross_retailer_visibility(
    session: Session,
    *,
    top_n: int = 1,
    keyword: str | None = None,
) -> list[CrossRetailerVisibilityRow]:
    return list_cross_retailer_visibility(session, top_n=top_n, keyword=keyword)
