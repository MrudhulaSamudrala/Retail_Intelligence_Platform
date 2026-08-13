"""Mercado Libre product-universe relevance helpers.

Delegates to the formal two-stage ``classification`` module.
"""

from __future__ import annotations

from typing import Optional

from collector.retailers.mercadolibre.classification import (
    EXCLUDED,
    VALID,
    ClassificationResult,
    classify_mercadolibre_product,
    is_collection_eligible,
)

SUPPORTED_PRODUCT_TYPES = frozenset(
    {"notebook", "desktop", "workstation", "tablet", "cpu", "gpu"}
)


def classify_candidate(
    *,
    title: Optional[str],
    category_raw: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
    discovery_name: Optional[str] = None,
) -> ClassificationResult:
    return classify_mercadolibre_product(
        title=title,
        category_raw=category_raw,
        specs=specs,
        discovery_name=discovery_name,
    )


def title_looks_irrelevant(title: Optional[str]) -> bool:
    """True when a listing title is not collection-eligible."""
    if not title or not title.strip():
        return True
    result = classify_mercadolibre_product(title=title, category_raw="notebook_ofertas")
    return not is_collection_eligible(result)


def is_in_collection_scope(
    *,
    product_type: Optional[str] = None,
    title: Optional[str] = None,
    classification: ClassificationResult | None = None,
) -> bool:
    """Valid ML collection target: VALID supported computing type."""
    if classification is not None:
        return is_collection_eligible(classification)
    result = classify_mercadolibre_product(title=title)
    if product_type and product_type in SUPPORTED_PRODUCT_TYPES:
        # Trust title classification over a stale forced type.
        return is_collection_eligible(result)
    return is_collection_eligible(result)


def classify_relevance(
    *,
    product_type: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """Return VALID | EXCLUDED | UNKNOWN for reporting."""
    result = classify_mercadolibre_product(title=title)
    if result.status == EXCLUDED:
        return "EXCLUDED"
    if result.status == VALID:
        return "VALID"
    return "UNKNOWN"
