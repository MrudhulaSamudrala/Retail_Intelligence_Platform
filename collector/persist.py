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
from collector.audit.models import AuditCheckResult, ListingEvidence
from collector.audit.engine import (
    build_context_for_product,
    build_product_evidence_from_normalized,
    persist_audit_results,
    run_audits,
)
from collector.parsers.badges import (
    BadgeEvaluation,
    BadgeEvidence,
    detect_promotional_badges,
    evaluate_badges,
    evaluation_rows,
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

    def save_badges(
        self,
        product: NormalizedProduct,
        *,
        product_id: int,
        collection_run_id: int,
        evidence: BadgeEvidence | None = None,
        include_promotional: bool = True,
        use_ocr_fallback: bool | None = None,
        observed_at: datetime | None = None,
        screenshot_path: str | None = None,
    ) -> BadgeEvaluation:
        """Evaluate platform badges and append rows to the ``badges`` table.

        Uses DOM/text/alt/title evidence first. OCR is only consulted when the
        optional fallback layer is enabled.
        """
        observed = observed_at or datetime.now(timezone.utc)
        evidence = evidence or BadgeEvidence(source_url=product.source_url)
        if evidence.source_url is None:
            evidence.source_url = product.source_url
        if screenshot_path and not evidence.screenshot_path:
            evidence.screenshot_path = screenshot_path

        specs = None
        if product.raw_payload:
            specs = product.raw_payload.get("specifications") or product.raw_payload.get(
                "specs"
            )

        evaluation = evaluate_badges(
            processor=product.processor,
            title=product.title,
            specifications=specs,
            brand=product.brand,
            evidence=evidence,
            use_ocr_fallback=use_ocr_fallback,
        )

        rows = evaluation_rows(evaluation)
        if include_promotional:
            promo_texts = list(evidence.badge_texts) + list(evidence.element_texts)
            if evidence.page_text:
                promo_texts.append(evidence.page_text)
            rows.extend(detect_promotional_badges(promo_texts))

        for row in rows:
            self.observations.add_badge(
                product_id=product_id,
                collection_run_id=collection_run_id,
                observed_at=observed,
                badge_code=row["badge_code"],
                badge_text=row["badge_text"],
                is_relevant=row.get("is_relevant"),
                relevance_notes=row.get("relevance_notes"),
                screenshot_path=evidence.screenshot_path,
                source_url=evidence.source_url or product.source_url,
            )

        self.session.flush()
        logger.info(
            "badges_persisted",
            extra={
                "event": "badges_persisted",
                "sku": product.retailer_sku,
                "product_id": product_id,
                "expected": evaluation.expected,
                "detected": evaluation.detected,
                "correct": evaluation.correct,
                "missing": evaluation.missing,
                "ambiguous": evaluation.ambiguous,
                "count": len(rows),
            },
        )
        return evaluation

    def save_audits(
        self,
        product: NormalizedProduct,
        *,
        product_id: int,
        collection_run_id: int,
        listing: ListingEvidence | None = None,
        badge_texts: list[str] | None = None,
        brand_media_signals: list[str] | None = None,
        oem_media_signals: list[str] | None = None,
        page_text: str | None = None,
        badges_inspected: bool = False,
        media_inspected: bool = False,
        selectors_used: list[str] | None = None,
        screenshot_path: str | None = None,
        observed_at: datetime | None = None,
    ) -> list[AuditCheckResult]:
        """Evaluate S1–P5 from provided evidence and append ``retailer_audits`` rows.

        Does not compute overall compliance score. Missing badge/media inspection
        flags remain UNKNOWN rather than PASS.
        """
        product_evidence = build_product_evidence_from_normalized(
            product,
            badge_texts=badge_texts,
            brand_media_signals=brand_media_signals,
            oem_media_signals=oem_media_signals,
            page_text=page_text,
            badges_inspected=badges_inspected,
            media_inspected=media_inspected,
            selectors_used=selectors_used,
            screenshot_path=screenshot_path,
        )
        ctx = build_context_for_product(
            product,
            product_id=product_id,
            collection_run_id=collection_run_id,
            listing=listing,
            product_evidence=product_evidence,
            observed_at=observed_at,
        )
        results = run_audits(ctx)
        persist_audit_results(self.session, ctx, results)
        return results
