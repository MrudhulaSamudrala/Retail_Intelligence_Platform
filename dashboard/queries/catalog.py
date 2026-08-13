"""Catalog / SKU explorer queries (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from dashboard.filters import DashboardFilters
from database.models import (
    Badge,
    BannerObservation,
    PriceHistory,
    Product,
    ProductSnapshot,
    RetailerAudit,
    SearchObservation,
)


@dataclass
class SkuRow:
    product_id: int
    title: Optional[str]
    retailer_sku: Optional[str]
    brand: Optional[str]
    oem: Optional[str]
    retailer_code: str
    country_code: str
    product_type: Optional[str]
    url: Optional[str]
    current_price: Optional[Decimal]
    currency: Optional[str]
    discount_pct: Optional[Decimal]
    last_observed_at: Optional[datetime]
    evidence_status: Optional[str]
    is_active: bool


def list_sku_rows(
    session: Session,
    *,
    filters: DashboardFilters,
    search: str = "",
    limit: int = 500,
) -> list[SkuRow]:
    stmt = select(Product).where(Product.is_active.is_(True))
    if filters.retailer_code:
        stmt = stmt.where(Product.retailer_code == filters.retailer_code)
    if filters.country_code:
        stmt = stmt.where(Product.country_code == filters.country_code)
    if filters.product_type:
        stmt = stmt.where(Product.product_type == filters.product_type)
    if filters.brand:
        stmt = stmt.where(Product.brand == filters.brand)
    if filters.oem:
        stmt = stmt.where(Product.oem == filters.oem)
    if search.strip():
        q = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Product.title.ilike(q),
                Product.retailer_sku.ilike(q),
                Product.brand.ilike(q),
                Product.oem.ilike(q),
            )
        )
    stmt = stmt.order_by(Product.retailer_code, Product.brand, Product.title).limit(limit)
    products = list(session.scalars(stmt).all())
    if not products:
        return []

    ids = [p.id for p in products]
    latest_price = (
        select(
            PriceHistory.product_id.label("product_id"),
            func.max(PriceHistory.observed_at).label("max_obs"),
        )
        .group_by(PriceHistory.product_id)
        .subquery()
    )
    price_rows = session.scalars(
        select(PriceHistory)
        .join(
            latest_price,
            and_(
                PriceHistory.product_id == latest_price.c.product_id,
                PriceHistory.observed_at == latest_price.c.max_obs,
            ),
        )
        .where(PriceHistory.product_id.in_(ids))
    ).all()
    price_by_id = {p.product_id: p for p in price_rows}

    latest_snap = (
        select(
            ProductSnapshot.product_id.label("product_id"),
            func.max(ProductSnapshot.observed_at).label("max_obs"),
        )
        .group_by(ProductSnapshot.product_id)
        .subquery()
    )
    snap_rows = session.scalars(
        select(ProductSnapshot)
        .join(
            latest_snap,
            and_(
                ProductSnapshot.product_id == latest_snap.c.product_id,
                ProductSnapshot.observed_at == latest_snap.c.max_obs,
            ),
        )
        .where(ProductSnapshot.product_id.in_(ids))
    ).all()
    snap_by_id = {s.product_id: s for s in snap_rows}

    out: list[SkuRow] = []
    for p in products:
        ph = price_by_id.get(p.id)
        snap = snap_by_id.get(p.id)
        evidence = None
        if snap is not None and isinstance(snap.raw_payload, dict):
            ev = snap.raw_payload.get("evidence")
            if isinstance(ev, dict):
                evidence = ev.get("status") or ev.get("access_status")
        out.append(
            SkuRow(
                product_id=p.id,
                title=p.title,
                retailer_sku=p.retailer_sku,
                brand=p.brand,
                oem=p.oem,
                retailer_code=p.retailer_code,
                country_code=p.country_code,
                product_type=p.product_type,
                url=p.canonical_url,
                current_price=ph.price_amount if ph else (snap.price_amount if snap else None),
                currency=ph.currency if ph else (snap.currency if snap else None),
                discount_pct=ph.discount_pct if ph else (snap.discount_pct if snap else None),
                last_observed_at=(
                    ph.observed_at
                    if ph
                    else (snap.observed_at if snap else p.last_seen_at)
                ),
                evidence_status=evidence,
                is_active=bool(p.is_active),
            )
        )
    return out


def product_detail(session: Session, product_id: int) -> dict[str, Any]:
    product = session.get(Product, product_id)
    if product is None:
        return {"error": "Product not found", "product_id": product_id}

    prices = list(
        session.scalars(
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.observed_at.desc())
            .limit(100)
        ).all()
    )
    audits = list(
        session.scalars(
            select(RetailerAudit)
            .where(RetailerAudit.product_id == product_id)
            .order_by(RetailerAudit.observed_at.desc())
            .limit(50)
        ).all()
    )
    badges = list(
        session.scalars(
            select(Badge)
            .where(Badge.product_id == product_id)
            .order_by(Badge.observed_at.desc())
            .limit(50)
        ).all()
    )
    snapshots = list(
        session.scalars(
            select(ProductSnapshot)
            .where(ProductSnapshot.product_id == product_id)
            .order_by(ProductSnapshot.observed_at.desc())
            .limit(20)
        ).all()
    )
    searches = list(
        session.scalars(
            select(SearchObservation)
            .where(
                or_(
                    SearchObservation.product_id == product_id,
                    and_(
                        SearchObservation.retailer_sku == product.retailer_sku,
                        SearchObservation.retailer_code == product.retailer_code,
                    ),
                )
            )
            .order_by(SearchObservation.observed_at.desc())
            .limit(50)
        ).all()
    )
    banners = list(
        session.scalars(
            select(BannerObservation)
            .where(BannerObservation.retailer_code == product.retailer_code)
            .where(BannerObservation.country_code == product.country_code)
            .order_by(BannerObservation.observed_at.desc())
            .limit(20)
        ).all()
    )

    evidence = None
    image_url = None
    if snapshots and isinstance(snapshots[0].raw_payload, dict):
        payload = snapshots[0].raw_payload
        evidence = payload.get("evidence")
        image_url = (
            payload.get("image_url")
            or payload.get("thumbnail")
            or (payload.get("images") or [None])[0]
        )

    return {
        "product": product,
        "prices": prices,
        "audits": audits,
        "badges": badges,
        "snapshots": snapshots,
        "searches": searches,
        "banners": banners,
        "evidence": evidence,
        "image_url": image_url,
    }
