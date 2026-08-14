"""Load current Brand Compliance scoring inputs from ``retailer_audits``.

Does not rewrite historical rows. Current KPIs use the latest audit per
``(product_id, check_code)`` among eligible computing products in the latest
collection_run per retailer/country (when ``collection_run_id`` is present).

S1–P5 evaluation logic is unchanged. Brand OTHER is skipped for tracked-brand
checks (S1–P4). UNKNOWN brand does not invent Intel/AMD/Qualcomm/Apple
requirements.

Newegg stratified catalog collection does not persist S1–P5; this loader never
fabricates audits from search observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from analytics.compliance.models import AuditScoreRow
from collector.parsers.badges import BadgeEvidence, BadgeEvaluation, evaluate_badges
from database.models import Product, RetailerAudit

TRACKED_BRANDS = frozenset({"Intel", "AMD", "Qualcomm", "Apple"})
TRACKED_BRAND_CHECKS = frozenset({"S1", "S2", "P1", "P2", "P3", "P4"})
UNKNOWN = "UNKNOWN"
OTHER = "OTHER"
NEWEGG_STRATIFIED_AUDIT_LIMITATION = (
    "Newegg stratified collection currently lacks persisted S1–P5 audit rows."
)


def _product_is_compliance_eligible(product: Product) -> bool:
    """Eligible computing types via the shared classifier. Brands are not a gate."""
    from collector.retailers.mercadolibre.classification import (
        EXCLUDED,
        OTHER_TYPE,
        SUPPORTED_PRODUCT_TYPES,
        classify_mercadolibre_product,
        is_collection_eligible,
    )

    stored = (product.product_type or "").strip().lower()
    if stored == OTHER_TYPE:
        return False
    classified = classify_mercadolibre_product(
        title=product.title, category_raw=product.category_raw
    )
    if classified.status == EXCLUDED or classified.hard_negative:
        return False
    if classified.product_type == OTHER_TYPE:
        return False
    if stored in SUPPORTED_PRODUCT_TYPES:
        return True
    return is_collection_eligible(classified)


def _latest_audit_batch_run_ids(session: Session) -> dict[tuple[str, str], int]:
    """Latest ``collection_run_id`` per retailer/country from audit timestamps.

    Audits without ``collection_run_id`` are not used to invent a batch.
    """
    ranked = (
        select(
            RetailerAudit.retailer_code.label("retailer_code"),
            RetailerAudit.country_code.label("country_code"),
            RetailerAudit.collection_run_id.label("run_id"),
            func.row_number()
            .over(
                partition_by=(RetailerAudit.retailer_code, RetailerAudit.country_code),
                order_by=(RetailerAudit.observed_at.desc(), RetailerAudit.id.desc()),
            )
            .label("rn"),
        )
        .where(RetailerAudit.collection_run_id.is_not(None))
    ).subquery("latest_audit_run")
    rows = session.execute(
        select(
            ranked.c.retailer_code,
            ranked.c.country_code,
            ranked.c.run_id,
        ).where(ranked.c.rn == 1)
    ).all()
    return {
        (str(retailer), str(country)): int(run_id)
        for retailer, country, run_id in rows
        if run_id is not None
    }


def _latest_audit_ids_stmt(*, run_ids: Sequence[int] | None):
    stmt = select(
        RetailerAudit.id.label("audit_id"),
        func.row_number()
        .over(
            partition_by=(RetailerAudit.product_id, RetailerAudit.check_code),
            order_by=(RetailerAudit.observed_at.desc(), RetailerAudit.id.desc()),
        )
        .label("rn"),
    ).where(RetailerAudit.product_id.is_not(None))
    if run_ids:
        stmt = stmt.where(RetailerAudit.collection_run_id.in_(list(run_ids)))
    ranked = stmt.subquery("latest_audits")
    return select(ranked.c.audit_id).where(ranked.c.rn == 1)


def _include_current_audit(audit: RetailerAudit) -> bool:
    brand = (audit.brand or "").strip()
    check = audit.check_code
    if brand == OTHER and check in TRACKED_BRAND_CHECKS:
        return False
    if brand == UNKNOWN and check in TRACKED_BRAND_CHECKS and audit.result in {
        "PASS",
        "FAIL",
    }:
        # Do not let invented tracked-brand PASS/FAIL enter the current score.
        return False
    return True


def load_audit_rows(
    session: Session,
    *,
    current_universe: bool = True,
    collection_run_ids: Sequence[int] | None = None,
) -> list[AuditScoreRow]:
    """Map ``retailer_audits`` into scoring inputs.

    When ``collection_run_ids`` is set, only those runs are scored (latest row
    per product/check within that set). When ``current_universe`` is True and
    run IDs are omitted, keep the latest row per ``(product_id, check_code)``
    for eligible products in the latest audit collection_run per
    retailer/country. Historical rows are not deleted.
    """
    if collection_run_ids:
        run_ids = [int(rid) for rid in collection_run_ids]
        if not run_ids:
            return []
        latest_ids = _latest_audit_ids_stmt(run_ids=run_ids)
        stmt = (
            select(RetailerAudit, Product)
            .join(Product, Product.id == RetailerAudit.product_id)
            .where(RetailerAudit.id.in_(latest_ids))
            .where(RetailerAudit.collection_run_id.in_(run_ids))
        )
        out: list[AuditScoreRow] = []
        for audit, product in session.execute(stmt).all():
            if not _product_is_compliance_eligible(product):
                continue
            if not _include_current_audit(audit):
                continue
            out.append(_to_score_row(audit, product_type=product.product_type))
        return out

    if not current_universe:
        audits = session.scalars(
            select(RetailerAudit).order_by(RetailerAudit.observed_at.asc())
        ).all()
        return [_to_score_row(a) for a in audits]

    run_map = _latest_audit_batch_run_ids(session)
    run_ids = list(run_map.values())
    if not run_ids:
        return []

    latest_ids = _latest_audit_ids_stmt(run_ids=run_ids)
    stmt = (
        select(RetailerAudit, Product)
        .join(Product, Product.id == RetailerAudit.product_id)
        .where(RetailerAudit.id.in_(latest_ids))
        .where(
            and_(
                RetailerAudit.collection_run_id.in_(run_ids),
            )
        )
    )
    out: list[AuditScoreRow] = []
    for audit, product in session.execute(stmt).all():
        if not _product_is_compliance_eligible(product):
            continue
        if not _include_current_audit(audit):
            continue
        expected_run = run_map.get((audit.retailer_code, audit.country_code))
        if expected_run is not None and audit.collection_run_id != expected_run:
            continue
        out.append(_to_score_row(audit, product_type=product.product_type))
    return out


def _to_score_row(
    audit: RetailerAudit, *, product_type: Optional[str] = None
) -> AuditScoreRow:
    return AuditScoreRow(
        brand=audit.brand,
        retailer_code=audit.retailer_code,
        country_code=audit.country_code,
        product_type=product_type if product_type is not None else audit.product_type,
        check_code=audit.check_code,
        result=audit.result,
        product_id=audit.product_id,
    )


def badge_evidence_was_inspected(evidence: BadgeEvidence | None) -> bool:
    if evidence is None:
        return False
    return any(
        [
            bool(evidence.badge_texts),
            bool(evidence.img_alts),
            bool(evidence.img_titles),
            bool(evidence.element_titles),
            bool(evidence.element_texts),
            bool((evidence.page_text or "").strip()),
            bool(evidence.ocr_texts),
        ]
    )


@dataclass(frozen=True)
class PlatformBadgeView:
    """Existing badge detector, with missing claimed only when inspectable."""

    evaluation: BadgeEvaluation
    inspected: bool
    expected: tuple[str, ...]
    detected: tuple[str, ...]
    correct: tuple[str, ...]
    missing: tuple[str, ...]
    unknown_families: tuple[str, ...]
    status: str  # correct | missing | unknown | not_expected


def evaluate_platform_badges(
    *,
    processor: Optional[str] = None,
    title: Optional[str] = None,
    specifications: Optional[object] = None,
    description: Optional[str] = None,
    brand: Optional[str] = None,
    evidence: BadgeEvidence | None = None,
) -> PlatformBadgeView:
    """Reuse ``evaluate_badges``; do not invent missing when not inspected."""
    inspected = badge_evidence_was_inspected(evidence)
    evaluation = evaluate_badges(
        processor=processor,
        title=title,
        specifications=specifications,
        description=description,
        brand=brand,
        evidence=evidence or BadgeEvidence(),
    )
    expected = tuple(evaluation.expected)
    detected = tuple(evaluation.detected)
    correct = tuple(evaluation.correct)
    if not inspected:
        missing: tuple[str, ...] = ()
        unknown_families = expected
        status = "unknown"
    else:
        missing = tuple(evaluation.missing)
        unknown_families = tuple(evaluation.ambiguous)
        if correct and not missing:
            status = "correct"
        elif missing:
            status = "missing"
        elif expected:
            status = "unknown"
        else:
            status = "not_expected"
    return PlatformBadgeView(
        evaluation=evaluation,
        inspected=inspected,
        expected=expected,
        detected=detected,
        correct=correct,
        missing=missing,
        unknown_families=unknown_families,
        status=status,
    )
