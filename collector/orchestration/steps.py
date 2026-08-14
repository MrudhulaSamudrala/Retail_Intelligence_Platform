"""Component step wrappers that call existing collectors (no logic duplication)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from collector.orchestration.config import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    OrchestrationConfig,
)

logger = logging.getLogger("collector.orchestration.steps")


def product_collection_step_status(outcome: Any) -> str:
    """Map a collector outcome to orchestration status.

    BLOCKED / PARTIAL completeness is never SUCCESS, even if some SKUs saved.
    """
    universe = getattr(outcome, "universe", None) or {}
    completeness = str(universe.get("completeness") or "").upper()
    search_status = str(universe.get("search_status") or "").upper()
    outcome_status = str(getattr(outcome, "status", None) or "").lower()
    bot_blocked = bool(getattr(outcome, "bot_blocked", False))
    failed = getattr(outcome, "failed", None) or []

    if outcome_status == "failed" or completeness == "FAILED":
        return STATUS_FAILED
    blocked = (
        bot_blocked
        or completeness in {"BLOCKED", "PARTIAL"}
        or search_status == "BLOCKED"
        or outcome_status == "partial"
        or (not universe and bool(failed))
    )
    if blocked:
        return STATUS_PARTIAL
    return STATUS_SUCCESS


@dataclass
class StepResult:
    component: str
    status: str
    records_processed: int = 0
    error_message: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


async def with_retries(
    operation: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    label: str,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "step_retry",
                extra={
                    "event": "step_retry",
                    "component": label,
                    "attempt": attempt,
                    "error": str(exc),
                },
            )
            if attempt >= max_attempts:
                break
            await asyncio.sleep(base_delay_seconds * (2 ** (attempt - 1)))
    assert last_exc is not None
    raise last_exc


async def run_newegg_products(
    session: Session, *, limit: int, parent_run_id: int
) -> StepResult:
    from collector.pipeline import CollectionPipeline
    from collector.retailers.newegg import build_collector
    from database.models import PriceHistory, Product, ProductSnapshot

    started = datetime.now(timezone.utc)
    before = session.scalar(select(func.count()).select_from(Product)) or 0
    before_snaps = session.scalar(select(func.count()).select_from(ProductSnapshot)) or 0
    before_prices = session.scalar(select(func.count()).select_from(PriceHistory)) or 0

    collector = build_collector()
    pipeline = CollectionPipeline(session, collector)
    outcome = await pipeline.run(limit=limit)

    after = session.scalar(select(func.count()).select_from(Product)) or 0
    after_snaps = session.scalar(select(func.count()).select_from(ProductSnapshot)) or 0
    after_prices = session.scalar(select(func.count()).select_from(PriceHistory)) or 0

    new_products = max(int(after) - int(before), 0)
    reobserved = max(len(outcome.success) - new_products, 0)
    status = product_collection_step_status(outcome)

    return StepResult(
        component="newegg",
        status=status,
        records_processed=outcome.observed or len(outcome.success),
        error_message="; ".join(
            str(e.get("error") if isinstance(e, dict) else e)
            for e in (outcome.failed or [])[:5]
        )
        or None,
        details={
            "parent_run_id": parent_run_id,
            "child_collection_run_id": outcome.collection_run_id,
            "products_before": int(before),
            "products_after": int(after),
            "new_products": new_products,
            "existing_reobserved": reobserved,
            "snapshots_created": int(after_snaps) - int(before_snaps),
            "price_rows_created": int(after_prices) - int(before_prices),
            "skipped_duplicates": len(outcome.skipped_duplicates),
            "bot_blocked": outcome.bot_blocked,
            "universe": outcome.universe or None,
            "requested": outcome.requested,
            "observed": outcome.observed,
        },
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )


async def run_mercadolibre_products(
    session: Session, *, limit: int, parent_run_id: int
) -> StepResult:
    from collector.pipeline import CollectionPipeline
    from collector.retailers.mercadolibre import build_collector
    from database.models import PriceHistory, Product, ProductSnapshot

    started = datetime.now(timezone.utc)
    before = session.scalar(select(func.count()).select_from(Product)) or 0
    before_snaps = session.scalar(select(func.count()).select_from(ProductSnapshot)) or 0
    before_prices = session.scalar(select(func.count()).select_from(PriceHistory)) or 0
    before_ml = (
        session.scalar(
            select(func.count()).select_from(Product).where(
                Product.retailer_code == "mercadolibre"
            )
        )
        or 0
    )

    collector = build_collector()
    pipeline = CollectionPipeline(session, collector)
    outcome = await pipeline.run(limit=limit)

    after = session.scalar(select(func.count()).select_from(Product)) or 0
    after_snaps = session.scalar(select(func.count()).select_from(ProductSnapshot)) or 0
    after_prices = session.scalar(select(func.count()).select_from(PriceHistory)) or 0
    after_ml = (
        session.scalar(
            select(func.count()).select_from(Product).where(
                Product.retailer_code == "mercadolibre"
            )
        )
        or 0
    )

    new_products = max(int(after_ml) - int(before_ml), 0)
    reobserved = max(len(outcome.success) - new_products, 0)
    status = product_collection_step_status(outcome)

    return StepResult(
        component="mercadolibre",
        status=status,
        records_processed=outcome.observed or len(outcome.success),
        error_message="; ".join(
            str(e.get("error") if isinstance(e, dict) else e)
            for e in (outcome.failed or [])[:5]
        )
        or None,
        details={
            "parent_run_id": parent_run_id,
            "child_collection_run_id": outcome.collection_run_id,
            "products_before": int(before),
            "products_after": int(after),
            "mercadolibre_products_before": int(before_ml),
            "mercadolibre_products_after": int(after_ml),
            "new_products": new_products,
            "existing_reobserved": reobserved,
            "snapshots_created": int(after_snaps) - int(before_snaps),
            "price_rows_created": int(after_prices) - int(before_prices),
            "skipped_duplicates": len(outcome.skipped_duplicates),
            "bot_blocked": outcome.bot_blocked,
            "universe": outcome.universe or None,
            "requested": outcome.requested,
            "observed": outcome.observed,
        },
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )


async def run_audits_step(
    session: Session, *, parent_run_id: int, enabled: bool = True
) -> StepResult:
    started = datetime.now(timezone.utc)
    if not enabled:
        return StepResult(
            component="audits",
            status=STATUS_SKIPPED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )
    from collector.audit.run_newegg_existing import run_audit as run_audit_newegg
    from collector.audit.run_mercadolibre_existing import run_audit as run_audit_ml

    summaries = []
    errors: list[str] = []
    rows = 0
    audited = 0
    for label, runner in (
        ("newegg", run_audit_newegg),
        ("mercadolibre", run_audit_ml),
    ):
        try:
            summary = await runner()
            summaries.append({label: summary})
            rows += int(summary.get("rows_inserted") or 0)
            audited += int(summary.get("products_audited") or 0)
            for err in summary.get("errors") or []:
                errors.append(f"{label}:{err}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}:{type(exc).__name__}: {exc}")

    if audited == 0 and errors:
        status = STATUS_FAILED
    elif errors:
        status = STATUS_PARTIAL
    else:
        status = STATUS_SUCCESS
    return StepResult(
        component="audits",
        status=status,
        records_processed=rows,
        error_message="; ".join(errors[:8]) or None,
        details={"parent_run_id": parent_run_id, "summaries": summaries},
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )


async def run_badges_step(
    session: Session, *, parent_run_id: int, enabled: bool = True
) -> StepResult:
    started = datetime.now(timezone.utc)
    if not enabled:
        return StepResult(
            component="badges",
            status=STATUS_SKIPPED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )
    from collector.badges.run_existing import run_badge_collection as run_badges_newegg
    from collector.badges.run_mercadolibre_existing import (
        run_badge_collection as run_badges_ml,
    )

    errors: list[str] = []
    rows = 0
    processed = 0
    failed = 0
    details: dict[str, Any] = {"parent_run_id": parent_run_id}
    for label, runner in (
        ("newegg", run_badges_newegg),
        ("mercadolibre", run_badges_ml),
    ):
        try:
            summary = await runner()
            details[label] = {
                k: summary.get(k)
                for k in (
                    "collection_run_id",
                    "processed",
                    "failed",
                    "badge_rows_inserted",
                )
            }
            rows += int(summary.get("badge_rows_inserted") or 0)
            processed += int(summary.get("processed") or 0)
            failed += int(summary.get("failed") or 0)
            for err in summary.get("errors") or []:
                errors.append(f"{label}:{err}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}:{type(exc).__name__}: {exc}")
            failed += 1

    if processed == 0 and (errors or failed):
        status = STATUS_FAILED
    elif failed or errors:
        status = STATUS_PARTIAL
    else:
        status = STATUS_SUCCESS
    return StepResult(
        component="badges",
        status=status,
        records_processed=rows,
        error_message="; ".join(errors[:8]) or None,
        details=details,
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )


async def run_pricing_step(
    session: Session, *, parent_run_id: int, product_step_details: dict[str, Any]
) -> StepResult:
    """Pricing/promotions are persisted during product collection (append-only).

    This step validates that historical price rows were created for the product
    steps and reports counts — without rediscovering a separate product universe.
    """
    started = datetime.now(timezone.utc)
    created = int(product_step_details.get("price_rows_created") or 0)
    snaps = int(product_step_details.get("snapshots_created") or 0)
    if created > 0 or snaps > 0:
        status = STATUS_SUCCESS
        err = None
    elif product_step_details.get("newegg_failed"):
        status = STATUS_FAILED
        err = "no_price_rows_product_collection_failed"
    else:
        status = STATUS_PARTIAL
        err = "no_new_price_rows_in_this_run"
    return StepResult(
        component="pricing",
        status=status,
        records_processed=created,
        error_message=err,
        details={
            "parent_run_id": parent_run_id,
            "price_rows_created": created,
            "snapshots_created": snaps,
            "note": (
                "prices/promotions append via CollectionPersister.save_product "
                "during product collection (upsert products, append history)"
            ),
        },
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )


async def run_banners_step(
    session: Session,
    *,
    parent_run_id: int,
    enabled: bool = True,
    retailer_codes: Sequence[str] | None = None,
) -> StepResult:
    started = datetime.now(timezone.utc)
    if not enabled:
        return StepResult(
            component="banners",
            status=STATUS_SKIPPED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )
    from collector.banners.collect import collect_homepage_banners
    from collector.banners.persist import persist_banners
    from database.repositories import CollectionRunRepository

    results = await collect_homepage_banners(retailer_codes=retailer_codes)
    total = 0
    success = 0
    failed = 0
    errors: list[str] = []
    for result in results:
        if result.inspected:
            success += 1
            runs = CollectionRunRepository(session)
            crow = runs.start(
                retailer_code=result.retailer_code,
                country_code=result.country_code,
                run_type="banner",
                run_metadata={
                    "parent_run_id": parent_run_id,
                    "source": "collector.orchestration",
                },
            )
            session.flush()
            rows = persist_banners(
                session,
                result.banners,
                retailer_code=result.retailer_code,
                country_code=result.country_code,
                collection_run_id=crow.id,
                observed_at=result.observed_at,
            )
            runs.complete(crow, status="completed", items_collected=len(rows))
            session.commit()
            total += len(rows)
        else:
            failed += 1
            errors.append(f"{result.retailer_code}:{result.error}")
    if success and failed:
        status = STATUS_PARTIAL
    elif success:
        status = STATUS_SUCCESS
    else:
        status = STATUS_FAILED
    return StepResult(
        component="banners",
        status=status,
        records_processed=total,
        error_message="; ".join(errors[:5]) or None,
        details={"parent_run_id": parent_run_id, "retailers_ok": success, "retailers_failed": failed},
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )


async def run_search_step(
    session: Session,
    *,
    parent_run_id: int,
    limit_per_retailer: int,
    enabled: bool = True,
) -> StepResult:
    started = datetime.now(timezone.utc)
    if not enabled:
        return StepResult(
            component="search",
            status=STATUS_SKIPPED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
        )
    from collector.search.collect import collect_search_visibility
    from collector.search.persist import persist_search_run

    results = await collect_search_visibility(limit_per_retailer=limit_per_retailer)
    total = 0
    complete = partial = failed = zero = 0
    for run in results:
        n = persist_search_run(session, run)
        session.commit()
        total += n
        st = run.collection_status
        if st == "COMPLETE":
            complete += 1
        elif st == "PARTIAL":
            partial += 1
        elif st == "FAILED":
            failed += 1
        elif st == "ZERO_RESULTS":
            zero += 1
    if complete and (partial or failed or zero):
        status = STATUS_PARTIAL
    elif complete and not failed:
        status = STATUS_SUCCESS
    elif total > 0:
        status = STATUS_PARTIAL
    else:
        status = STATUS_FAILED if failed else STATUS_PARTIAL
    return StepResult(
        component="search",
        status=status,
        records_processed=total,
        error_message=None
        if not failed
        else f"failed_searches={failed}; zero_results={zero}",
        details={
            "parent_run_id": parent_run_id,
            "searches": len(results),
            "complete": complete,
            "partial": partial,
            "failed": failed,
            "zero_results": zero,
        },
        started_at=started,
        completed_at=datetime.now(timezone.utc),
    )
