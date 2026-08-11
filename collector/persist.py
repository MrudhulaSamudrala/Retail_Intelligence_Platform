"""Persist normalized collection results into the SQLAlchemy data layer."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from collector.normalize import NormalizedProduct
from database.repositories import (
    CollectionRunRepository,
    ObservationRepository,
    ProductRepository,
)

logger = logging.getLogger("collector.persist")


class CollectionPersister:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.runs = CollectionRunRepository(session)
        self.products = ProductRepository(session)
        self.observations = ObservationRepository(session)

    def start_run(
        self,
        *,
        retailer_code: str,
        country_code: str,
        run_type: str = "pricing",
        limit: int | None = None,
    ):
        return self.runs.start(
            retailer_code=retailer_code,
            country_code=country_code,
            run_type=run_type,
            run_metadata={"limit": limit, "source": "collector.run"},
        )

    def complete_run(self, run, *, status: str, items_collected: int, error_message: Optional[str] = None):
        return self.runs.complete(
            run,
            status=status,
            items_collected=items_collected,
            error_message=error_message,
        )

    def save_product(
        self,
        product: NormalizedProduct,
        *,
        collection_run_id: int,
        observed_at: datetime | None = None,
    ) -> int:
        """Upsert identity and append historical observations. Returns product id."""
        observed = observed_at or datetime.now(timezone.utc)
        row = self.products.upsert_identity(
            retailer_code=product.retailer_code,
            country_code=product.country_code,
            retailer_sku=product.retailer_sku,
            canonical_url=product.source_url,
            title=product.title,
            brand=product.brand,
            oem=product.oem,
            product_type=product.product_type,
            category_raw=product.category_raw,
            collection_run_id=collection_run_id,
        )

        self.observations.add_snapshot(
            product_id=row.id,
            collection_run_id=collection_run_id,
            observed_at=observed,
            title=product.title,
            brand=product.brand,
            oem=product.oem,
            product_type=product.product_type,
            category_raw=product.category_raw,
            availability=product.availability,
            price_amount=product.price_amount,
            currency=product.currency,
            source_url=product.source_url,
            raw_payload=product.raw_payload,
        )

        if product.price_amount is not None and product.currency:
            self.observations.add_price(
                product_id=row.id,
                collection_run_id=collection_run_id,
                observed_at=observed,
                price_amount=product.price_amount,
                list_price=product.list_price,
                currency=product.currency,
                discount_amount=product.discount_amount,
                discount_pct=product.discount_pct,
                is_on_promotion=product.is_on_promotion,
            )

        if product.promo_text or product.is_on_promotion:
            self.observations.add_promotion(
                product_id=row.id,
                collection_run_id=collection_run_id,
                observed_at=observed,
                promo_type=product.promo_type,
                promo_text=product.promo_text,
                discount_value=product.discount_amount,
                discount_unit="amount" if product.discount_amount is not None else None,
                raw_text=product.promo_text,
            )

        self.session.flush()
        logger.info(
            "product_persisted",
            extra={
                "event": "product_persisted",
                "sku": product.retailer_sku,
                "url": product.source_url,
                "retailer": product.retailer_code,
                "country": product.country_code,
            },
        )
        return row.id
