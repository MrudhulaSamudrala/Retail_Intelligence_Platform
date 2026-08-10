"""Repository helpers for append-only observation persistence.

These helpers intentionally avoid inserting sample/fake data.
Collectors and analytics layers should call them with real observations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Badge,
    BannerObservation,
    CollectionRun,
    PriceHistory,
    Product,
    ProductSnapshot,
    Promotion,
    RetailerAudit,
    SearchObservation,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CollectionRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(
        self,
        *,
        retailer_code: str,
        country_code: str,
        run_type: str,
        run_metadata: Optional[dict[str, Any]] = None,
    ) -> CollectionRun:
        run = CollectionRun(
            retailer_code=retailer_code,
            country_code=country_code,
            run_type=run_type,
            status="running",
            started_at=_utcnow(),
            run_metadata=run_metadata,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def complete(
        self,
        run: CollectionRun,
        *,
        status: str = "completed",
        items_collected: int = 0,
        error_message: Optional[str] = None,
    ) -> CollectionRun:
        run.status = status
        run.items_collected = items_collected
        run.error_message = error_message
        run.completed_at = _utcnow()
        self.session.flush()
        return run


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_retailer_sku(
        self, retailer_code: str, country_code: str, retailer_sku: str
    ) -> Optional[Product]:
        stmt = select(Product).where(
            Product.retailer_code == retailer_code,
            Product.country_code == country_code,
            Product.retailer_sku == retailer_sku,
        )
        return self.session.scalars(stmt).first()

    def upsert_identity(
        self,
        *,
        retailer_code: str,
        country_code: str,
        retailer_sku: str,
        canonical_url: str,
        title: Optional[str] = None,
        brand: Optional[str] = None,
        oem: Optional[str] = None,
        product_type: Optional[str] = None,
        category_raw: Optional[str] = None,
        collection_run_id: Optional[int] = None,
    ) -> Product:
        """Update mutable latest attributes; historical detail lives in snapshots."""
        product = self.get_by_retailer_sku(retailer_code, country_code, retailer_sku)
        now = _utcnow()
        if product is None:
            product = Product(
                retailer_code=retailer_code,
                country_code=country_code,
                retailer_sku=retailer_sku,
                canonical_url=canonical_url,
                title=title,
                brand=brand,
                oem=oem,
                product_type=product_type,
                category_raw=category_raw,
                first_seen_at=now,
                last_seen_at=now,
                last_collection_run_id=collection_run_id,
            )
            self.session.add(product)
        else:
            product.canonical_url = canonical_url
            if title is not None:
                product.title = title
            if brand is not None:
                product.brand = brand
            if oem is not None:
                product.oem = oem
            if product_type is not None:
                product.product_type = product_type
            if category_raw is not None:
                product.category_raw = category_raw
            product.last_seen_at = now
            product.last_collection_run_id = collection_run_id
            product.is_active = True
        self.session.flush()
        return product


class ObservationRepository:
    """Append-only writers for historical observation tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_snapshot(self, **kwargs: Any) -> ProductSnapshot:
        row = ProductSnapshot(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    def add_price(self, **kwargs: Any) -> PriceHistory:
        row = PriceHistory(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    def add_promotion(self, **kwargs: Any) -> Promotion:
        row = Promotion(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    def add_audit(self, **kwargs: Any) -> RetailerAudit:
        row = RetailerAudit(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    def add_badge(self, **kwargs: Any) -> Badge:
        row = Badge(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    def add_banner(self, **kwargs: Any) -> BannerObservation:
        row = BannerObservation(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    def add_search(self, **kwargs: Any) -> SearchObservation:
        row = SearchObservation(**kwargs)
        if row.observed_at is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row
