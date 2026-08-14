"""Append-only persistence for search visibility observations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from collector.search.models import SearchRunResult
from database.models import SearchObservation
from database.repositories import CollectionRunRepository, ObservationRepository, ProductRepository

# Distinguish historical keyword SoV rows from stratified catalog SERP slots.
SOURCE_KEYWORD_SEARCH = "keyword_search"
SOURCE_STRATIFIED_CATALOG = "stratified_catalog"


def is_stratified_catalog_observation(row: SearchObservation | Mapping[str, Any]) -> bool:
    """True for new catalog-universe rows; False for historical keyword searches."""
    source = None
    details: Any = None
    if isinstance(row, Mapping):
        source = row.get("observation_source")
        details = row.get("details")
    else:
        source = getattr(row, "observation_source", None)
        details = getattr(row, "details", None)
    if source == SOURCE_STRATIFIED_CATALOG:
        return True
    if isinstance(details, dict) and details.get("observation_source") == SOURCE_STRATIFIED_CATALOG:
        return True
    return False


def stratum_observation_status(report: Mapping[str, Any] | None) -> str:
    """Map a catalog stratum report to COMPLETE|PARTIAL|BLOCKED|FAILED.

    Fallback/ofertas cards are never COMPLETE ranked SERP evidence.
    """
    report = report or {}
    if report.get("used_fallback"):
        return "PARTIAL"
    search_status = str(report.get("search_status") or "")
    completeness = str(report.get("completeness") or "")
    observed = int(report.get("observed") or 0)
    if search_status == "BLOCKED":
        if observed <= 0:
            return "BLOCKED"
        return "PARTIAL"
    if completeness in {"COMPLETE", "PARTIAL", "FAILED", "BLOCKED"}:
        return completeness
    return "FAILED"


def persist_search_run(
    session: Session,
    run: SearchRunResult,
    *,
    collection_run_id: int | None = None,
) -> int:
    """Append all hits for a keyword search. Returns number of rows written.

    Never updates prior observations. Even FAILED/ZERO runs may persist zero rows
    while the collection_run metadata records the outcome.
    """
    observed = run.observed_at or datetime.now(timezone.utc)
    runs = CollectionRunRepository(session)
    if collection_run_id is None:
        crow = runs.start(
            retailer_code=run.retailer_code,
            country_code=run.country_code,
            run_type="search",
            run_metadata={
                "keyword": run.keyword,
                "collection_status": run.collection_status,
                "pages_collected": run.pages_collected,
                "pagination_reliable": run.pagination_reliable,
                "search_url": run.search_url,
                "error": run.error,
            },
        )
        session.flush()
        collection_run_id = crow.id
        run_obj = crow
    else:
        run_obj = runs.get(collection_run_id)

    obs = ObservationRepository(session)
    products = ProductRepository(session)
    for hit in run.hits:
        product_id = None
        sku = (hit.retailer_sku or "").strip()
        if sku:
            existing = products.get_by_retailer_sku(
                hit.retailer_code, hit.country_code, sku
            )
            if existing is not None:
                product_id = existing.id

        details = dict(hit.details or {})
        details.setdefault("observation_source", SOURCE_KEYWORD_SEARCH)
        details.setdefault("ranking_scope", "keyword_search")
        # Eligibility flag: preserve observation, allow analytics to exclude junk.
        if "is_eligible" not in details:
            if hit.retailer_code == "mercadolibre":
                from collector.retailers.mercadolibre.classification import (
                    is_collection_eligible,
                    classify_mercadolibre_product,
                )

                classified = classify_mercadolibre_product(title=hit.title)
                details["is_eligible"] = is_collection_eligible(classified)
                details["classification_status"] = classified.status
            else:
                details["is_eligible"] = True

        obs.add_search(
            collection_run_id=collection_run_id,
            product_id=product_id,
            observed_at=observed,
            retailer_code=hit.retailer_code,
            country_code=hit.country_code,
            keyword=hit.keyword,
            position=hit.position,
            page_number=hit.page_number,
            retailer_sku=hit.retailer_sku,
            title=hit.title,
            brand=hit.brand,
            oem=hit.oem,
            source_url=hit.source_url,
            is_sponsored=bool(hit.is_sponsored),
            evidence_text=hit.evidence_text,
            selector=hit.selector,
            collection_status=run.collection_status,
            search_url=hit.search_url or run.search_url,
            pages_collected=run.pages_collected,
            stratum=None,
            observation_source=SOURCE_KEYWORD_SEARCH,
            details=details or None,
        )

    if run_obj is not None:
        status = "completed"
        if run.collection_status == "FAILED":
            status = "failed"
        elif run.collection_status == "PARTIAL":
            status = "partial"
        runs.complete(
            run_obj,
            status=status,
            items_collected=len(run.hits),
            error_message=run.error,
        )
    session.flush()
    return len(run.hits)


def _optional_int(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def persist_stratified_catalog_observations(
    session: Session,
    *,
    collection_run_id: int,
    retailer_code: str,
    country_code: str,
    slots: Sequence[Mapping[str, Any]],
    strata_reports: Sequence[Mapping[str, Any]] | None = None,
    observed_at: datetime | None = None,
) -> int:
    """Append one search_observations row per catalog SERP slot.

    Does not create product identities. Native ``search_position`` is stored as
    ``position``; ``universe_slot`` is metadata only. Never deletes prior rows.
    Excluded, duplicate, failed, unknown, and inaccessible slots are written.
    """
    observed = observed_at or datetime.now(timezone.utc)
    obs = ObservationRepository(session)
    products = ProductRepository(session)
    reports = {str(r.get("stratum") or ""): r for r in (strata_reports or [])}
    written = 0
    for index, slot in enumerate(slots, start=1):
        stratum = slot.get("stratum")
        report = reports.get(str(stratum or "")) or {}
        status = stratum_observation_status(report)
        query = str(slot.get("query") or report.get("query") or stratum or "")
        position = _optional_int(slot.get("search_position"))
        if position is None or position < 1:
            position = _optional_int(slot.get("position"))
        if position is None or position < 1:
            # Last resort: keep a row for every observed slot (do not drop SERP evidence).
            position = index
        sku = (slot.get("sku") or slot.get("retailer_sku") or "") or None
        if sku:
            sku = str(sku).strip() or None
        product_id = slot.get("product_id")
        if product_id is None and sku:
            existing = products.get_by_retailer_sku(retailer_code, country_code, sku)
            if existing is not None:
                product_id = existing.id
        bucket = str(slot.get("bucket") or slot.get("extraction_status") or "")
        details: dict[str, Any] = {
            "observation_source": SOURCE_STRATIFIED_CATALOG,
            "ranking_scope": "stratum_query",
            "stratum": stratum,
            "query": query,
            "universe_slot": slot.get("universe_slot"),
            "universe_slot_is_retailer_rank": False,
            "slot_status": bucket,
            "extraction_status": slot.get("extraction_status"),
            "exclusion_status": slot.get("exclusion_status") or bucket,
            "exclusion_reason": slot.get("exclusion_reason"),
            "duplicate": bucket == "DUPLICATE",
            "excluded": bucket == "EXCLUDED",
            "is_eligible": bucket == "VALID",
            "gaming": bool(slot.get("gaming")),
            "used_fallback": bool(slot.get("used_fallback") or report.get("used_fallback")),
            "product_type": slot.get("product_type"),
            "search_position": position,
            "collection_run_id": collection_run_id,
        }
        obs.add_search(
            collection_run_id=collection_run_id,
            product_id=int(product_id) if product_id is not None else None,
            observed_at=observed,
            retailer_code=retailer_code,
            country_code=country_code,
            keyword=query,
            position=position,
            page_number=_optional_int(slot.get("search_page") or slot.get("page_number")),
            retailer_sku=sku,
            title=slot.get("title"),
            brand=slot.get("brand"),
            oem=slot.get("oem"),
            source_url=slot.get("url") or slot.get("source_url"),
            is_sponsored=bool(slot.get("is_sponsored")),
            evidence_text=slot.get("title"),
            selector=None,
            collection_status=status,
            search_url=slot.get("search_url") or report.get("search_url"),
            pages_collected=_optional_int(
                report.get("pages_inspected") or report.get("pages_collected")
            ),
            stratum=str(stratum) if stratum else None,
            observation_source=SOURCE_STRATIFIED_CATALOG,
            details=details,
        )
        written += 1
    session.flush()
    return written

