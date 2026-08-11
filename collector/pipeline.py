"""Common collection pipeline shared by all retailer adapters."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from collector.base import CollectionOutcome, RetailerCollector
from collector.browser import BrowserSession
from collector.persist import CollectionPersister

logger = logging.getLogger("collector.pipeline")


class CollectionPipeline:
    """Discover → enrich → normalize (adapter) → dedupe → persist."""

    def __init__(self, session: Session, collector: RetailerCollector) -> None:
        self.session = session
        self.collector = collector
        self.persister = CollectionPersister(session)

    async def run(self, *, limit: int = 20) -> CollectionOutcome:
        outcome = CollectionOutcome()
        seen_skus: set[str] = set()
        run = self.persister.start_run(
            retailer_code=self.collector.code,
            country_code=self.collector.country_code,
            run_type="pricing",
            limit=limit,
        )
        self.session.commit()
        run_id = run.id
        error_message: Optional[str] = None

        logger.info(
            "collection_started",
            extra={
                "event": "collection_started",
                "retailer": self.collector.code,
                "country": self.collector.country_code,
                "run_id": run_id,
                "count": limit,
            },
        )

        try:
            async with BrowserSession() as browser:
                candidates = await self.collector.discover_listings(browser, limit=limit * 2)
                outcome.discovered = len(candidates)
                logger.info(
                    "listings_discovered",
                    extra={
                        "event": "listings_discovered",
                        "retailer": self.collector.code,
                        "run_id": run_id,
                        "count": len(candidates),
                    },
                )

                for candidate in candidates:
                    if len(outcome.success) >= limit:
                        break

                    sku = candidate.retailer_sku.strip()
                    if not sku:
                        outcome.failed.append(
                            {"url": candidate.source_url, "error": "missing_sku"}
                        )
                        continue
                    if sku in seen_skus:
                        outcome.skipped_duplicates.append(sku)
                        continue
                    seen_skus.add(sku)

                    try:
                        product = await self.collector.fetch_product(browser, candidate)
                        observed_at = datetime.now(timezone.utc)
                        self.persister.save_product(
                            product,
                            collection_run_id=run_id,
                            observed_at=observed_at,
                        )
                        self.session.commit()
                        outcome.success.append(product)
                    except Exception as exc:  # noqa: BLE001 - per-product isolation
                        self.session.rollback()
                        logger.exception(
                            "product_failed",
                            extra={
                                "event": "product_failed",
                                "retailer": self.collector.code,
                                "run_id": run_id,
                                "sku": sku,
                                "url": candidate.source_url,
                                "error": str(exc),
                            },
                        )
                        outcome.failed.append(
                            {
                                "sku": sku,
                                "url": candidate.source_url,
                                "error": str(exc),
                            }
                        )
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            if "bot challenge" in error_message.lower() or "unusual traffic" in error_message.lower():
                outcome.bot_blocked = True
            logger.exception(
                "collection_aborted",
                extra={
                    "event": "collection_aborted",
                    "retailer": self.collector.code,
                    "run_id": run_id,
                    "error": error_message,
                },
            )

        status = "completed"
        if error_message and not outcome.success:
            status = "failed"
        elif outcome.failed or error_message:
            status = "partial"

        # Re-attach run after possible rollbacks.
        from database.models import CollectionRun

        run_row = self.session.get(CollectionRun, run_id)
        if run_row is not None:
            self.persister.complete_run(
                run_row,
                status=status,
                items_collected=len(outcome.success),
                error_message=error_message,
            )
            self.session.commit()

        outcome.collection_run_id = run_id
        outcome.status = status
        logger.info(
            "collection_finished",
            extra={
                "event": "collection_finished",
                "retailer": self.collector.code,
                "run_id": run_id,
                "count": len(outcome.success),
            },
        )
        return outcome
