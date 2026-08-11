"""Audit engine orchestration and persistence into retailer_audits."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from collector.audit.checks import evaluate_all_checks
from collector.audit.models import (
    AuditCheckResult,
    AuditContext,
    ListingEvidence,
    ProductEvidence,
)
from collector.normalize import NormalizedProduct
from database.repositories import ObservationRepository

logger = logging.getLogger("collector.audit.engine")


def build_product_evidence_from_normalized(
    product: NormalizedProduct,
    *,
    badge_texts: Optional[list[str]] = None,
    brand_media_signals: Optional[list[str]] = None,
    oem_media_signals: Optional[list[str]] = None,
    page_text: Optional[str] = None,
    badges_inspected: bool = False,
    media_inspected: bool = False,
    selectors_used: Optional[list[str]] = None,
    screenshot_path: Optional[str] = None,
) -> ProductEvidence:
    """Build product-page evidence from a normalized product (+ optional DOM signals).

    Specs from ``raw_payload['specs']`` or top-level processor fields are used for P3.
    Badge/media flags default to not-inspected so missing DOM evidence stays UNKNOWN.
    """
    specs: dict[str, str] = {}
    raw = product.raw_payload or {}
    raw_specs = raw.get("specs")
    if isinstance(raw_specs, dict):
        specs = {str(k): str(v) for k, v in raw_specs.items() if v is not None}
    # Ensure key extracted fields participate in P3 even if specs table was sparse.
    for key, value in (
        ("processor", product.processor),
        ("gpu", product.gpu),
        ("ram", product.ram),
        ("storage", product.storage),
    ):
        if value and key not in specs:
            specs[key] = value

    specs_available = bool(specs) or bool(raw.get("specs") == {})
    # If collector explicitly stored an empty specs dict after inspection, treat as available.
    if isinstance(raw_specs, dict):
        specs_available = True

    return ProductEvidence(
        title=product.title,
        specs=specs,
        specs_available=specs_available,
        page_text=page_text,
        badge_texts=list(badge_texts or []),
        brand_media_signals=list(brand_media_signals or []),
        oem_media_signals=list(oem_media_signals or []),
        media_inspected=media_inspected,
        badges_inspected=badges_inspected,
        selectors_used=list(selectors_used or []),
        source_url=product.source_url,
        screenshot_path=screenshot_path,
        available=True,
    )


def build_context_for_product(
    product: NormalizedProduct,
    *,
    product_id: Optional[int] = None,
    collection_run_id: Optional[int] = None,
    listing: Optional[ListingEvidence] = None,
    product_evidence: Optional[ProductEvidence] = None,
    observed_at: Optional[datetime] = None,
) -> AuditContext:
    return AuditContext(
        retailer_code=product.retailer_code,
        country_code=product.country_code,
        brand=product.brand,
        oem=product.oem,
        product_type=product.product_type,
        product_id=product_id,
        collection_run_id=collection_run_id,
        observed_at=observed_at or datetime.now(timezone.utc),
        listing=listing,
        product=product_evidence
        or build_product_evidence_from_normalized(product),
    )


def run_audits(ctx: AuditContext) -> list[AuditCheckResult]:
    """Evaluate S1–P5 independently. Does not compute overall compliance score."""
    results = evaluate_all_checks(ctx)
    logger.info(
        "audits_evaluated",
        extra={
            "event": "audits_evaluated",
            "retailer": ctx.retailer_code,
            "brand": ctx.brand,
            "count": len(results),
        },
    )
    return results


def persist_audit_results(
    session: Session,
    ctx: AuditContext,
    results: Sequence[AuditCheckResult],
) -> list[Any]:
    """Append audit check rows to ``retailer_audits`` (never updates prior rows)."""
    observations = ObservationRepository(session)
    observed_at = ctx.observed_at or datetime.now(timezone.utc)
    rows = []
    for item in results:
        row = observations.add_audit(
            product_id=ctx.product_id,
            collection_run_id=ctx.collection_run_id,
            observed_at=observed_at,
            retailer_code=ctx.retailer_code,
            country_code=ctx.country_code,
            brand=ctx.brand,
            product_type=ctx.product_type,
            check_code=item.check_code,
            result=item.result,
            evidence_text=item.evidence_text,
            screenshot_path=item.screenshot_path,
            source_url=item.source_url,
            details=item.details,
        )
        rows.append(row)
    session.flush()
    return rows


def evaluate_and_persist(
    session: Session,
    ctx: AuditContext,
) -> list[AuditCheckResult]:
    results = run_audits(ctx)
    persist_audit_results(session, ctx, results)
    return results
