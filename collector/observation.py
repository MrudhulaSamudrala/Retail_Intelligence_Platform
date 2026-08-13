"""Observed-result universe accounting for catalog collection.

Mercado Libre ``--limit N`` means the first N observable SERP results.
Newegg keeps the legacy valid-product quota (this module is unused there).
"""

from __future__ import annotations

from typing import Any, Optional

from collector.normalize import NormalizedProduct
from collector.retailers.mercadolibre.classification import (
    EXCLUDED,
    SUPPORTED_PRODUCT_TYPES,
    VALID,
    classify_mercadolibre_product,
    is_collection_eligible,
)

STATUS_VALID = "VALID"
STATUS_EXCLUDED = "EXCLUDED"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_FAILED = "FAILED"
STATUS_DUPLICATE = "DUPLICATE"

COMPLETENESS_COMPLETE = "COMPLETE"
COMPLETENESS_PARTIAL = "PARTIAL"
COMPLETENESS_FAILED = "FAILED"


def observation_bucket(
    product: Optional[NormalizedProduct] = None,
    *,
    duplicate: bool = False,
    failed: bool = False,
) -> str:
    """Map one SERP slot to VALID | EXCLUDED | UNKNOWN | FAILED | DUPLICATE."""
    if failed:
        return STATUS_FAILED
    if duplicate:
        return STATUS_DUPLICATE
    if product is None:
        return STATUS_UNKNOWN
    raw = product.raw_payload or {}
    classification = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
    status = str(classification.get("status") or "")
    ptype = str(
        classification.get("product_type") or product.product_type or ""
    ).strip().lower()
    if status == EXCLUDED or ptype == "other":
        return STATUS_EXCLUDED
    result = classify_mercadolibre_product(
        title=product.title,
        category_raw=product.category_raw,
        specs=(raw.get("specs") if isinstance(raw.get("specs"), dict) else None),
    )
    if classification:
        result.status = status or result.status
        result.product_type = str(classification.get("product_type") or result.product_type)
        result.gaming = bool(classification.get("gaming", result.gaming))
        result.hard_negative = bool(classification.get("hard_negative", result.hard_negative))
    if is_collection_eligible(result) or (
        result.status == VALID and ptype in SUPPORTED_PRODUCT_TYPES
    ):
        return STATUS_VALID
    if result.status == EXCLUDED:
        return STATUS_EXCLUDED
    return STATUS_UNKNOWN


def completeness_status(*, requested: int, observed: int, had_error: bool) -> str:
    """COMPLETE only when the requested observation depth was reached."""
    if observed <= 0 and had_error:
        return COMPLETENESS_FAILED
    if requested > 0 and observed >= requested:
        return COMPLETENESS_COMPLETE
    if observed > 0:
        return COMPLETENESS_PARTIAL
    return COMPLETENESS_FAILED


def run_status_from_completeness(completeness: str) -> str:
    if completeness == COMPLETENESS_COMPLETE:
        return "completed"
    if completeness == COMPLETENESS_PARTIAL:
        return "partial"
    return "failed"


def is_eligible_gaming(product: NormalizedProduct) -> bool:
    raw = product.raw_payload or {}
    classification = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
    gaming = classification.get("gaming")
    if gaming is None:
        gaming = str(raw.get("gaming_relevance") or "").lower() == "gaming"
    if not gaming:
        return False
    return observation_bucket(product) == STATUS_VALID


def universe_dict(
    *,
    requested: int,
    observed: int,
    valid: int,
    excluded: int,
    unknown: int,
    failed: int,
    duplicate: int,
    eligible_gaming: int,
    completeness: str,
) -> dict[str, Any]:
    return {
        "requested": requested,
        "observed": observed,
        "valid": valid,
        "excluded": excluded,
        "unknown": unknown,
        "failed": failed,
        "duplicate": duplicate,
        "eligible_gaming": eligible_gaming,
        "completeness": completeness,
        "reconciles": observed == valid + excluded + unknown + failed + duplicate,
    }
