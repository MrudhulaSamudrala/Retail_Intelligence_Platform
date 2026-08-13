"""Append-only persistence for search visibility observations."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from collector.search.models import SearchRunResult
from database.repositories import CollectionRunRepository, ObservationRepository, ProductRepository


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
            details=hit.details or None,
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
