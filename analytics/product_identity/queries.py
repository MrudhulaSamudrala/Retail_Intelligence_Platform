"""Read-side queries for retailer counts and cross-retailer availability."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from analytics.product_identity.matching import MATCHED, POSSIBLE_MATCH, UNMATCHED
from database.models import CanonicalProduct, Product, ProductCrosswalk


@dataclass(frozen=True)
class RetailerProductCounts:
    newegg: int
    mercadolibre: int
    total_retailer_records: int
    matched_pairs: int
    possible_matches: int
    unmatched: int
    newegg_only: int
    mercadolibre_only: int
    common_products: int
    unique_canonical_products: int


@dataclass(frozen=True)
class AvailabilityMatrixRow:
    canonical_product_id: int
    display_name: Optional[str]
    manufacturer_model: Optional[str]
    oem: Optional[str]
    on_newegg: bool
    on_mercadolibre: bool
    newegg_product_id: Optional[int]
    mercadolibre_product_id: Optional[int]
    match_status: str
    match_confidence: Optional[Decimal]
    match_method: Optional[str]


def retailer_product_counts(session: Session) -> RetailerProductCounts:
    """Retailer-specific counts remain independent of canonical matching."""
    newegg = int(
        session.scalar(
            select(func.count()).select_from(Product).where(
                Product.retailer_code == "newegg", Product.is_active.is_(True)
            )
        )
        or 0
    )
    ml = int(
        session.scalar(
            select(func.count()).select_from(Product).where(
                Product.retailer_code == "mercadolibre", Product.is_active.is_(True)
            )
        )
        or 0
    )

    matched_canon_ids = set(
        session.scalars(
            select(ProductCrosswalk.canonical_product_id).where(
                ProductCrosswalk.match_status == MATCHED
            )
        ).all()
    )
    # Count matched pairs = canonical ids that have >=2 MATCHED crosswalk rows
    common = 0
    for cid in matched_canon_ids:
        n = int(
            session.scalar(
                select(func.count()).select_from(ProductCrosswalk).where(
                    ProductCrosswalk.canonical_product_id == cid,
                    ProductCrosswalk.match_status == MATCHED,
                )
            )
            or 0
        )
        if n >= 2:
            common += 1

    possible = int(
        session.scalar(
            select(func.count(func.distinct(ProductCrosswalk.canonical_product_id))).where(
                ProductCrosswalk.match_status == POSSIBLE_MATCH
            )
        )
        or 0
    )
    unmatched = int(
        session.scalar(
            select(func.count()).select_from(ProductCrosswalk).where(
                ProductCrosswalk.match_status == UNMATCHED
            )
        )
        or 0
    )
    canon_total = int(
        session.scalar(select(func.count()).select_from(CanonicalProduct)) or 0
    )

    # Newegg-only / ML-only from unmatched singletons + unmatched crosswalk
    newegg_only = int(
        session.scalar(
            select(func.count())
            .select_from(ProductCrosswalk)
            .join(Product, Product.id == ProductCrosswalk.product_id)
            .where(
                ProductCrosswalk.match_status == UNMATCHED,
                Product.retailer_code == "newegg",
            )
        )
        or 0
    )
    ml_only = int(
        session.scalar(
            select(func.count())
            .select_from(ProductCrosswalk)
            .join(Product, Product.id == ProductCrosswalk.product_id)
            .where(
                ProductCrosswalk.match_status == UNMATCHED,
                Product.retailer_code == "mercadolibre",
            )
        )
        or 0
    )

    return RetailerProductCounts(
        newegg=newegg,
        mercadolibre=ml,
        total_retailer_records=newegg + ml,
        matched_pairs=common,
        possible_matches=possible,
        unmatched=unmatched,
        newegg_only=newegg_only,
        mercadolibre_only=ml_only,
        common_products=common,
        unique_canonical_products=canon_total,
    )


def crosswalk_summary(session: Session) -> dict[str, int]:
    counts = retailer_product_counts(session)
    return {
        "newegg_products": counts.newegg,
        "mercadolibre_products": counts.mercadolibre,
        "matched": counts.matched_pairs,
        "possible_matches": counts.possible_matches,
        "unmatched": counts.unmatched,
        "newegg_only": counts.newegg_only,
        "mercadolibre_only": counts.mercadolibre_only,
        "common_products": counts.common_products,
        "unique_canonical_products": counts.unique_canonical_products,
    }


def list_common_products(session: Session) -> Sequence[AvailabilityMatrixRow]:
    return [
        row
        for row in product_availability_matrix(session)
        if row.on_newegg and row.on_mercadolibre and row.match_status == MATCHED
    ]


def list_retailer_only_products(
    session: Session, *, retailer_code: str
) -> Sequence[AvailabilityMatrixRow]:
    rows = product_availability_matrix(session)
    if retailer_code == "newegg":
        return [r for r in rows if r.on_newegg and not r.on_mercadolibre]
    if retailer_code == "mercadolibre":
        return [r for r in rows if r.on_mercadolibre and not r.on_newegg]
    return []


def product_availability_matrix(session: Session) -> list[AvailabilityMatrixRow]:
    """One row per canonical product with YES/NO availability by retailer."""
    canons = session.scalars(select(CanonicalProduct).order_by(CanonicalProduct.id)).all()
    out: list[AvailabilityMatrixRow] = []
    for canon in canons:
        entries = session.scalars(
            select(ProductCrosswalk).where(
                ProductCrosswalk.canonical_product_id == canon.id
            )
        ).all()
        newegg_pid = None
        ml_pid = None
        status = UNMATCHED
        confidence = None
        method = None
        for entry in entries:
            product = session.get(Product, entry.product_id)
            if product is None:
                continue
            if product.retailer_code == "newegg":
                newegg_pid = product.id
            elif product.retailer_code == "mercadolibre":
                ml_pid = product.id
            if entry.match_status == MATCHED:
                status = MATCHED
            elif entry.match_status == POSSIBLE_MATCH and status != MATCHED:
                status = POSSIBLE_MATCH
            confidence = entry.match_confidence
            method = entry.match_method
        out.append(
            AvailabilityMatrixRow(
                canonical_product_id=int(canon.id),
                display_name=canon.model_name or canon.normalized_name,
                manufacturer_model=canon.manufacturer_model,
                oem=canon.oem,
                on_newegg=newegg_pid is not None,
                on_mercadolibre=ml_pid is not None,
                newegg_product_id=newegg_pid,
                mercadolibre_product_id=ml_pid,
                match_status=status,
                match_confidence=confidence,
                match_method=method,
            )
        )
    return out
