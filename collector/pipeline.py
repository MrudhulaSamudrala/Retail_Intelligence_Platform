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
            async with self._browser_session() as browser:
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
                        if not self.collector.is_in_collection_scope(product):
                            outcome.skipped_irrelevant.append(
                                {
                                    "sku": sku,
                                    "url": candidate.source_url,
                                    "title": product.title,
                                    "product_type": product.product_type,
                                    "reason": "out_of_collection_scope",
                                }
                            )
                            logger.info(
                                "product_skipped_irrelevant",
                                extra={
                                    "event": "product_skipped_irrelevant",
                                    "retailer": self.collector.code,
                                    "run_id": run_id,
                                    "sku": sku,
                                    "product_type": product.product_type,
                                },
                            )
                            continue
                        observed_at = datetime.now(timezone.utc)
                        product_id = self.persister.save_product(
                            product,
                            collection_run_id=run_id,
                            observed_at=observed_at,
                        )
                        self._persist_surface_evidence(
                            product,
                            product_id=product_id,
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

    def _browser_session(self) -> BrowserSession:
        factory = getattr(self.collector, "build_browser_session", None)
        if callable(factory):
            return factory()
        return BrowserSession()

    def _persist_surface_evidence(
        self,
        product,
        *,
        product_id: int,
        collection_run_id: int,
        observed_at,
    ) -> None:
        """Append badge/audit rows from captured ML evidence. Newegg is unchanged."""
        if product.retailer_code != "mercadolibre":
            return
        raw = product.raw_payload or {}
        listing = raw.get("listing_audit") if isinstance(raw.get("listing_audit"), dict) else {}
        pdp = raw.get("pdp_audit") if isinstance(raw.get("pdp_audit"), dict) else {}
        signals = raw.get("badge_signals") if isinstance(raw.get("badge_signals"), dict) else {}

        from collector.audit.models import ListingEvidence
        from collector.parsers.badges import BadgeEvidence

        listing_ev = ListingEvidence(
            title=listing.get("title") or product.title,
            tile_text=listing.get("tile_text"),
            badge_texts=list(listing.get("badge_texts") or []),
            selectors_used=list(listing.get("selectors_used") or ["listing_card"]),
            source_url=listing.get("source_url") or product.source_url,
            available=bool(listing.get("available", True)),
        )
        badge_texts = list(signals.get("badge_texts") or []) + list(
            pdp.get("badge_texts") or []
        )
        img_alts = list(signals.get("img_alts") or [])
        self.persister.save_badges(
            product,
            product_id=product_id,
            collection_run_id=collection_run_id,
            evidence=BadgeEvidence(
                badge_texts=badge_texts,
                img_alts=img_alts,
                img_titles=list(signals.get("img_titles") or []),
                element_titles=list(signals.get("element_titles") or [])
                + list(signals.get("aria_labels") or []),
                element_texts=list(signals.get("badge_texts") or []),
                page_text=None,
                source_url=product.source_url,
            ),
            observed_at=observed_at,
        )
        self.persister.save_audits(
            product,
            product_id=product_id,
            collection_run_id=collection_run_id,
            listing=listing_ev,
            badge_texts=badge_texts,
            brand_media_signals=list(pdp.get("brand_media_signals") or img_alts),
            oem_media_signals=list(pdp.get("oem_media_signals") or img_alts),
            page_text=None,
            badges_inspected=bool(pdp.get("badges_inspected")),
            media_inspected=bool(pdp.get("media_inspected")),
            selectors_used=list(pdp.get("selectors_used") or []),
            observed_at=observed_at,
        )

