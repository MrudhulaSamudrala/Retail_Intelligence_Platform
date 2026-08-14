"""Repository helpers for append-only observation persistence.

Design rules:
- Product identity may be upserted (latest attributes only).
- Observation tables are append-only — helpers never update prior rows.
- Query helpers return chronological history for analytics/dashboard use.

These helpers intentionally avoid inserting sample/fake production data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

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
        # Component collectors must not finalize the parent production run.
        # The orchestrator writes overall status after every step finishes.
        if (run.run_type or "").lower() == "production":
            return run
        run.status = status
        run.items_collected = items_collected
        run.error_message = error_message
        run.completed_at = _utcnow()
        self.session.flush()
        return run

    def get(self, run_id: int) -> Optional[CollectionRun]:
        return self.session.get(CollectionRun, run_id)

    def list_for_retailer(
        self,
        retailer_code: str,
        country_code: str,
        *,
        limit: int = 50,
    ) -> Sequence[CollectionRun]:
        stmt = (
            select(CollectionRun)
            .where(
                CollectionRun.retailer_code == retailer_code,
                CollectionRun.country_code == country_code,
            )
            .order_by(CollectionRun.started_at.desc())
            .limit(limit)
        )
        return self.session.scalars(stmt).all()


class ProductRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, product_id: int) -> Optional[Product]:
        return self.session.get(Product, product_id)

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

    def list_by_brand(
        self,
        brand: str,
        *,
        retailer_code: Optional[str] = None,
        country_code: Optional[str] = None,
        product_type: Optional[str] = None,
        limit: int = 200,
    ) -> Sequence[Product]:
        stmt = select(Product).where(Product.brand == brand, Product.is_active.is_(True))
        if retailer_code:
            stmt = stmt.where(Product.retailer_code == retailer_code)
        if country_code:
            stmt = stmt.where(Product.country_code == country_code)
        if product_type:
            stmt = stmt.where(Product.product_type == product_type)
        stmt = stmt.order_by(Product.last_seen_at.desc()).limit(limit)
        return self.session.scalars(stmt).all()


class ObservationRepository:
    """Append-only writers and chronological readers for observation tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- writers (append-only) -------------------------------------------------

    def add_snapshot(self, **kwargs: Any) -> ProductSnapshot:
        return self._append(ProductSnapshot, kwargs)

    def add_price(self, **kwargs: Any) -> PriceHistory:
        return self._append(PriceHistory, kwargs)

    def add_promotion(self, **kwargs: Any) -> Promotion:
        return self._append(Promotion, kwargs)

    def add_audit(self, **kwargs: Any) -> RetailerAudit:
        return self._append(RetailerAudit, kwargs)

    def add_badge(self, **kwargs: Any) -> Badge:
        return self._append(Badge, kwargs)

    def add_banner(self, **kwargs: Any) -> BannerObservation:
        return self._append(BannerObservation, kwargs)

    def add_search(self, **kwargs: Any) -> SearchObservation:
        return self._append(SearchObservation, kwargs)

    def _append(self, model: type, kwargs: dict[str, Any]) -> Any:
        row = model(**kwargs)
        if getattr(row, "observed_at", None) is None:
            row.observed_at = _utcnow()
        self.session.add(row)
        self.session.flush()
        return row

    # --- readers (chronological history) --------------------------------------

    def list_snapshots(
        self, product_id: int, *, limit: Optional[int] = None
    ) -> Sequence[ProductSnapshot]:
        stmt = (
            select(ProductSnapshot)
            .where(ProductSnapshot.product_id == product_id)
            .order_by(ProductSnapshot.observed_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def list_prices(
        self, product_id: int, *, limit: Optional[int] = None
    ) -> Sequence[PriceHistory]:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.observed_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def list_promotions(
        self, product_id: int, *, limit: Optional[int] = None
    ) -> Sequence[Promotion]:
        stmt = (
            select(Promotion)
            .where(Promotion.product_id == product_id)
            .order_by(Promotion.observed_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def list_audits(
        self,
        *,
        product_id: Optional[int] = None,
        retailer_code: Optional[str] = None,
        country_code: Optional[str] = None,
        brand: Optional[str] = None,
        check_code: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[RetailerAudit]:
        stmt = select(RetailerAudit)
        if product_id is not None:
            stmt = stmt.where(RetailerAudit.product_id == product_id)
        if retailer_code is not None:
            stmt = stmt.where(RetailerAudit.retailer_code == retailer_code)
        if country_code is not None:
            stmt = stmt.where(RetailerAudit.country_code == country_code)
        if brand is not None:
            stmt = stmt.where(RetailerAudit.brand == brand)
        if check_code is not None:
            stmt = stmt.where(RetailerAudit.check_code == check_code)
        stmt = stmt.order_by(RetailerAudit.observed_at.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def list_badges(
        self, product_id: int, *, limit: Optional[int] = None
    ) -> Sequence[Badge]:
        stmt = (
            select(Badge)
            .where(Badge.product_id == product_id)
            .order_by(Badge.observed_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def list_banners(
        self,
        retailer_code: str,
        country_code: str,
        *,
        limit: Optional[int] = None,
    ) -> Sequence[BannerObservation]:
        stmt = (
            select(BannerObservation)
            .where(
                BannerObservation.retailer_code == retailer_code,
                BannerObservation.country_code == country_code,
            )
            .order_by(BannerObservation.observed_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()

    def list_searches(
        self,
        retailer_code: str,
        country_code: str,
        keyword: str,
        *,
        limit: Optional[int] = None,
    ) -> Sequence[SearchObservation]:
        stmt = (
            select(SearchObservation)
            .where(
                SearchObservation.retailer_code == retailer_code,
                SearchObservation.country_code == country_code,
                SearchObservation.keyword == keyword,
            )
            .order_by(SearchObservation.observed_at.asc(), SearchObservation.position.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.session.scalars(stmt).all()
