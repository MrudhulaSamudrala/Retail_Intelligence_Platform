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
    processor: Optional[str] = None
    gpu: Optional[str] = None
    ram: Optional[str] = None
    storage: Optional[str] = None
    is_on_promotion: bool = False


_SPEC_ALIASES = {
    "processor": ("processor", "cpu", "cpu_model", "processador"),
    "gpu": ("gpu", "graphics", "graphics_card", "placa de video", "video card"),
    "ram": ("ram", "memory", "memoria", "memória"),
    "storage": ("storage", "ssd", "hdd", "disco", "armazenamento"),
}


def extract_product_specs(payload: Any) -> dict[str, Optional[str]]:
    """Pull display specs from a snapshot payload without inventing values."""
    data = payload if isinstance(payload, dict) else {}
    specs = data.get("specs") if isinstance(data.get("specs"), dict) else {}
    lowered = {str(k).strip().lower(): v for k, v in specs.items()}
    out: dict[str, Optional[str]] = {}
    for field, aliases in _SPEC_ALIASES.items():
        value = None
        for alias in aliases:
            raw = data.get(alias)
            if raw not in (None, "", []):
                value = str(raw).strip()
                break
            raw = lowered.get(alias)
            if raw not in (None, "", []):
                value = str(raw).strip()
                break
        out[field] = value or None
    return out


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
        specs = extract_product_specs(snap.raw_payload if snap is not None else None)
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
                processor=specs.get("processor"),
                gpu=specs.get("gpu"),
                ram=specs.get("ram"),
                storage=specs.get("storage"),
                is_on_promotion=bool(ph.is_on_promotion) if ph is not None else False,
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
    specs = extract_product_specs(None)
    if snapshots and isinstance(snapshots[0].raw_payload, dict):
        payload = snapshots[0].raw_payload
        specs = extract_product_specs(payload)
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
        "specs": specs,
    }


def attribute_coverage(session: Session, *, filters: DashboardFilters, limit: int = 2000) -> list[dict[str, Any]]:
    """Coverage of stored catalog attributes among latest product snapshots."""
    rows = list_sku_rows(session, filters=filters, limit=limit)
    total = len(rows)
    if total == 0:
        return []

    def _pct(present: int) -> float:
        return 100.0 * present / total

    present = {
        "Processor": sum(1 for r in rows if r.processor),
        "Graphics": sum(1 for r in rows if r.gpu),
        "RAM": sum(1 for r in rows if r.ram),
        "Storage": sum(1 for r in rows if r.storage),
        "Price": sum(1 for r in rows if r.current_price is not None),
        "Brand": sum(1 for r in rows if r.brand),
    }
    return [
        {"attribute": name, "coverage_pct": _pct(count), "present": count, "total": total}
        for name, count in present.items()
    ]


def badge_coverage_matrix(session: Session) -> list[dict[str, Any]]:
    """Brand × configured platform-family coverage from stored badge observations.

    N/A when a brand has no badge evidence. 0% when inspected products exist
    but the family was not detected.
    """
    from collector.config_loader import load_badges
    from dashboard.presentation import TRACKED_PLATFORM_BRANDS

    families = list(load_badges().get("platform_families") or [])
    products = list(
        session.scalars(select(Product).where(Product.is_active.is_(True))).all()
    )
    brand_ids: dict[str, set[int]] = {b: set() for b in TRACKED_PLATFORM_BRANDS}
    for product in products:
        if product.brand in brand_ids:
            brand_ids[product.brand].add(product.id)

    badge_rows = session.execute(select(Badge.product_id, Badge.badge_code)).all()
    evidence_ids: dict[str, set[int]] = {b: set() for b in TRACKED_PLATFORM_BRANDS}
    family_ids: dict[str, set[int]] = {str(f.get("code")): set() for f in families}
    product_brand = {p.id: p.brand for p in products}
    for product_id, code in badge_rows:
        brand = product_brand.get(product_id)
        if brand in evidence_ids:
            evidence_ids[brand].add(product_id)
        if code and code in family_ids:
            family_ids[code].add(product_id)

    out: list[dict[str, Any]] = []
    for family in families:
        brand = str(family.get("brand") or "")
        code = str(family.get("code") or "")
        name = str(family.get("name") or code)
        inspected = evidence_ids.get(brand) or set()
        if not inspected:
            out.append(
                {
                    "brand": brand,
                    "badge": name,
                    "code": code,
                    "rate": None,
                    "display": "N/A",
                    "state": "N/A",
                }
            )
            continue
        detected = len(family_ids.get(code, set()) & inspected)
        rate = detected / len(inspected)
        pct = rate * 100.0
        from dashboard.presentation import badge_coverage_state

        out.append(
            {
                "brand": brand,
                "badge": name,
                "code": code,
                "rate": rate,
                "display": f"{pct:.0f}%",
                "state": badge_coverage_state(rate),
            }
        )
    return out
