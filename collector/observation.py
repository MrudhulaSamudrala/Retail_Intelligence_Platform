"""Observed-result universe accounting for catalog collection.

``--limit N`` / production ``search_universe_size`` is the total number of
observable SERP positions across stratified generic gaming queries.
Classification happens after observation. Excluded, unknown, and duplicate
candidates still consume a position. Completeness is per stratum; overall
COMPLETE requires every stratum to reach its requested budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from collector.normalize import NormalizedProduct
from collector.retailers.mercadolibre.classification import (
    EXCLUDED,
    OTHER_TYPE,
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
STATUS_INACCESSIBLE = "INACCESSIBLE"

COMPLETENESS_COMPLETE = "COMPLETE"
COMPLETENESS_PARTIAL = "PARTIAL"
COMPLETENESS_FAILED = "FAILED"

SEARCH_OK = "OK"
SEARCH_BLOCKED = "BLOCKED"


def classify_observed_product(
    product: Optional[NormalizedProduct] = None,
    *,
    title: Optional[str] = None,
    category_raw: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
):
    """Retailer-agnostic product-type / eligibility classification."""
    if product is not None:
        raw = product.raw_payload or {}
        specs = specs or (raw.get("specs") if isinstance(raw.get("specs"), dict) else None)
        title = title or product.title
        category_raw = category_raw or product.category_raw
    return classify_mercadolibre_product(
        title=title,
        category_raw=category_raw,
        specs=specs,
    )


def observation_bucket(
    product: Optional[NormalizedProduct] = None,
    *,
    duplicate: bool = False,
    failed: bool = False,
    inaccessible: bool = False,
) -> str:
    """Map one SERP slot to VALID | EXCLUDED | UNKNOWN | FAILED | DUPLICATE | INACCESSIBLE."""
    if inaccessible:
        return STATUS_INACCESSIBLE
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
    if status == EXCLUDED or ptype == OTHER_TYPE:
        return STATUS_EXCLUDED
    result = classify_observed_product(product)
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


def completeness_status(
    *,
    requested: int,
    observed: int,
    had_error: bool,
    search_blocked: bool = False,
) -> str:
    """COMPLETE only when the requested observation depth was reached."""
    if requested > 0 and observed >= requested:
        return COMPLETENESS_COMPLETE
    if search_blocked:
        return COMPLETENESS_PARTIAL
    if observed <= 0 and had_error:
        return COMPLETENESS_FAILED
    if observed > 0:
        return COMPLETENESS_PARTIAL
    return COMPLETENESS_FAILED


def stratum_completeness(
    *,
    requested: int,
    observed: int,
    ranked_search_ok: bool = True,
    used_fallback: bool = False,
    search_blocked: bool = False,
    had_error: bool = False,
) -> str:
    """A stratum is COMPLETE only from its intended ranked search at budget.

    Ofertas/access fallback never upgrades a blocked ranked search to COMPLETE.
    """
    if used_fallback or search_blocked or not ranked_search_ok:
        return COMPLETENESS_PARTIAL
    return completeness_status(
        requested=requested,
        observed=observed,
        had_error=had_error,
        search_blocked=False,
    )


def overall_completeness_from_strata(
    strata: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    requested: int | None = None,
    observed: int | None = None,
    had_error: bool = False,
    search_blocked: bool = False,
) -> str:
    """Overall COMPLETE only when every configured stratum is COMPLETE."""
    reports = list(strata or [])
    if not reports:
        return completeness_status(
            requested=int(requested or 0),
            observed=int(observed or 0),
            had_error=had_error,
            search_blocked=search_blocked,
        )
    statuses = [str(item.get("completeness") or "") for item in reports]
    if statuses and all(status == COMPLETENESS_COMPLETE for status in statuses):
        return COMPLETENESS_COMPLETE
    if any(status == COMPLETENESS_PARTIAL for status in statuses) or any(
        int(item.get("observed") or 0) > 0 for item in reports
    ):
        return COMPLETENESS_PARTIAL
    if search_blocked or any(
        str(item.get("search_status") or "") == SEARCH_BLOCKED for item in reports
    ):
        return COMPLETENESS_PARTIAL
    if had_error:
        return COMPLETENESS_FAILED
    return COMPLETENESS_PARTIAL


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


def attach_observation_classification(product: NormalizedProduct) -> NormalizedProduct:
    """Stamp classification onto raw_payload without dropping existing evidence."""
    raw = dict(product.raw_payload or {})
    specs = raw.get("specs") if isinstance(raw.get("specs"), dict) else None
    result = classify_observed_product(product, specs=specs)
    existing = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
    if existing.get("status"):
        merged = dict(existing)
        merged.setdefault("product_type", result.product_type)
        merged.setdefault("gaming", result.gaming)
        raw["classification"] = merged
    else:
        raw["classification"] = result.to_dict()
        if result.product_type == OTHER_TYPE or (
            result.status == EXCLUDED and (not product.product_type or product.product_type == "UNKNOWN")
        ):
            product.product_type = result.product_type
        elif result.product_type in SUPPORTED_PRODUCT_TYPES and (
            not product.product_type or product.product_type == "UNKNOWN"
        ):
            product.product_type = result.product_type
    product.raw_payload = raw
    return product


@dataclass
class ObservationCounters:
    requested: int = 0
    observed: int = 0
    extracted: int = 0
    valid: int = 0
    excluded: int = 0
    unknown: int = 0
    failed: int = 0
    duplicate: int = 0
    inaccessible: int = 0
    eligible_gaming: int = 0
    buckets: list[str] = field(default_factory=list)

    def record(self, bucket: str, product: Optional[NormalizedProduct] = None) -> None:
        self.observed += 1
        self.buckets.append(bucket)
        if bucket in {STATUS_VALID, STATUS_EXCLUDED, STATUS_UNKNOWN}:
            self.extracted += 1
        if bucket == STATUS_VALID:
            self.valid += 1
            if product is not None and is_eligible_gaming(product):
                self.eligible_gaming += 1
        elif bucket == STATUS_EXCLUDED:
            self.excluded += 1
        elif bucket == STATUS_UNKNOWN:
            self.unknown += 1
        elif bucket == STATUS_FAILED:
            self.failed += 1
        elif bucket == STATUS_DUPLICATE:
            self.duplicate += 1
        elif bucket == STATUS_INACCESSIBLE:
            self.inaccessible += 1

    def completeness(self, *, had_error: bool, search_blocked: bool = False) -> str:
        return completeness_status(
            requested=self.requested,
            observed=self.observed,
            had_error=had_error,
            search_blocked=search_blocked,
        )

    def as_dict(self, *, completeness: str, **extra: Any) -> dict[str, Any]:
        payload = universe_dict(
            requested=self.requested,
            observed=self.observed,
            extracted=self.extracted,
            valid=self.valid,
            excluded=self.excluded,
            unknown=self.unknown,
            failed=self.failed,
            duplicate=self.duplicate,
            inaccessible=self.inaccessible,
            eligible_gaming=self.eligible_gaming,
            completeness=completeness,
        )
        payload.update(extra)
        return payload


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
    extracted: int | None = None,
    inaccessible: int = 0,
) -> dict[str, Any]:
    if extracted is None:
        extracted = valid + excluded + unknown
    return {
        "requested": requested,
        "observed": observed,
        "extracted": extracted,
        "valid": valid,
        "excluded": excluded,
        "unknown": unknown,
        "failed": failed,
        "duplicate": duplicate,
        "inaccessible": inaccessible,
        "eligible_gaming": eligible_gaming,
        "completeness": completeness,
        "inaccessible_scope": "candidate",
        "reconciliation_rule": (
            "observed = valid + excluded + unknown + failed + duplicate + inaccessible; "
            "buckets are mutually exclusive; inaccessible is candidate-level "
            "(blocked PDP/slot), not a second copy of pages_blocked"
        ),
        "reconciles": observed
        == valid + excluded + unknown + failed + duplicate + inaccessible,
    }
