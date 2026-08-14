"""Share of Shelf queries over real ``products`` / ``product_snapshots`` rows."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from analytics.share_of_shelf.models import (
    SosExclusionBreakdown,
    SosScope,
    SosShare,
    SosSnapshot,
    SosTrendPoint,
)
from analytics.share_of_shelf.universe import (
    INCLUSION_RULES_ID,
    EligibleListing,
    SosUniverseConfig,
    build_eligible_universe,
    load_sos_universe_config,
)
from collector.search.persist import SOURCE_STRATIFIED_CATALOG
from database.models import CollectionRun, Product, ProductSnapshot, SearchObservation

# Reproducible SQL sketch (Postgres) for the current stratified SoS universe:
SQL_CANDIDATE_PRODUCTS = """
-- Latest stratified collection_run per (retailer_code, country_code), then:
SELECT DISTINCT ON (p.retailer_code, p.country_code, p.retailer_sku)
       p.id AS product_id, p.retailer_code, p.country_code, p.retailer_sku,
       COALESCE(s.brand, p.brand) AS brand,
       COALESCE(s.oem, p.oem) AS oem,
       COALESCE(s.product_type, p.product_type) AS product_type,
       COALESCE(s.title, p.title) AS title,
       COALESCE(s.category_raw, p.category_raw) AS category_raw
FROM product_snapshots s
JOIN products p ON p.id = s.product_id
WHERE s.collection_run_id = :latest_stratified_run_id
"""


def _share(count: int, universe_size: int) -> Decimal:
    if universe_size <= 0:
        return Decimal("0")
    return (Decimal(count) / Decimal(universe_size)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _day_start(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _apply_scope_to_product_query(stmt: Any, scope: SosScope) -> Any:
    if scope.retailer_code is not None:
        stmt = stmt.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        stmt = stmt.where(Product.country_code == scope.country_code)
    if scope.product_type is not None:
        stmt = stmt.where(Product.product_type == scope.product_type)
    if scope.oem is not None:
        stmt = stmt.where(Product.oem == scope.oem)
    if scope.brand is not None:
        stmt = stmt.where(Product.brand == scope.brand)
    return stmt


def _universe_meta(run: CollectionRun) -> dict[str, Any]:
    meta = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    uni = meta.get("universe") if isinstance(meta, dict) else {}
    return uni if isinstance(uni, dict) else {}


def _run_has_strata_metadata(run: CollectionRun) -> bool:
    strata = _universe_meta(run).get("strata")
    if isinstance(strata, list) and len(strata) > 0:
        return True
    if isinstance(strata, dict) and len(strata) > 0:
        return True
    return False


def _stratified_observation_run_ids(session: Session) -> set[int]:
    rows = session.scalars(
        select(SearchObservation.collection_run_id)
        .where(SearchObservation.observation_source == SOURCE_STRATIFIED_CATALOG)
        .where(SearchObservation.collection_run_id.is_not(None))
        .distinct()
    ).all()
    return {int(rid) for rid in rows if rid is not None}


def _run_is_stratified(run: CollectionRun, *, stratified_obs_run_ids: set[int]) -> bool:
    if _run_has_strata_metadata(run):
        return True
    return int(run.id) in stratified_obs_run_ids


def _completeness_from_run(run: CollectionRun) -> str:
    uni = _universe_meta(run)
    if uni.get("used_fallback"):
        return "PARTIAL"
    strata = uni.get("strata")
    items: list[Any]
    if isinstance(strata, list):
        items = strata
    elif isinstance(strata, dict):
        items = list(strata.values())
    else:
        items = []
    for item in items:
        if isinstance(item, dict) and item.get("used_fallback"):
            return "PARTIAL"
        if isinstance(item, dict) and str(item.get("completeness") or "") == "PARTIAL":
            return "PARTIAL"
        if isinstance(item, dict) and str(item.get("search_url") or "").find("ofertas") >= 0:
            return "PARTIAL"
    status = str(uni.get("completeness") or "").upper()
    if status == "COMPLETE":
        return "COMPLETE"
    if status in {"PARTIAL", "FAILED", "BLOCKED"}:
        return "PARTIAL"
    run_status = (run.status or "").lower()
    if run_status in {"partial", "partial_success"}:
        return "PARTIAL"
    if run_status in {"completed", "success"}:
        # Ranked COMPLETE only when metadata says so; otherwise PARTIAL.
        if status == "COMPLETE":
            return "COMPLETE"
        if _run_has_strata_metadata(run) and status == "":
            return "PARTIAL"
        return "PARTIAL"
    return "PARTIAL"


def _combine_collection_status(statuses: list[str]) -> str:
    if not statuses:
        return "NO_DATA"
    unique = set(statuses)
    if unique == {"COMPLETE"}:
        return "COMPLETE"
    if "PARTIAL" in unique or "FAILED" in unique or "BLOCKED" in unique:
        return "PARTIAL"
    if "COMPLETE" in unique:
        return "PARTIAL"
    return "NO_DATA"


def _latest_stratified_runs(
    session: Session, scope: SosScope
) -> dict[tuple[str, str], CollectionRun]:
    """Latest stratified catalog collection per retailer/country."""
    stmt = select(CollectionRun)
    if scope.retailer_code is not None:
        stmt = stmt.where(CollectionRun.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        stmt = stmt.where(CollectionRun.country_code == scope.country_code)
    stmt = stmt.order_by(CollectionRun.started_at.desc(), CollectionRun.id.desc())
    stratified_obs = _stratified_observation_run_ids(session)
    latest: dict[tuple[str, str], CollectionRun] = {}
    for run in session.scalars(stmt).all():
        if not _run_is_stratified(run, stratified_obs_run_ids=stratified_obs):
            continue
        key = (run.retailer_code, run.country_code)
        if key not in latest:
            latest[key] = run
    return latest


def _candidate_from_product(
    product: Product,
    *,
    brand: Optional[str] = None,
    oem: Optional[str] = None,
    product_type: Optional[str] = None,
    title: Optional[str] = None,
    category_raw: Optional[str] = None,
    availability: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "product_id": product.id,
        "retailer_code": product.retailer_code,
        "country_code": product.country_code,
        "retailer_sku": product.retailer_sku,
        "brand": brand if brand is not None else product.brand,
        "oem": oem if oem is not None else product.oem,
        "product_type": product_type if product_type is not None else product.product_type,
        "title": title if title is not None else product.title,
        "category_raw": category_raw if category_raw is not None else product.category_raw,
        "availability": availability,
    }


def _load_candidates_from_current_batch(
    session: Session, scope: SosScope
) -> tuple[list[dict[str, Any]], int, dict[tuple[str, str], CollectionRun]]:
    """Products observed in the latest stratified collection; no historical padding."""
    if scope.collection_run_ids:
        scoped_runs = list(
            session.scalars(
                select(CollectionRun).where(
                    CollectionRun.id.in_(list(scope.collection_run_ids))
                )
            ).all()
        )
        runs: dict[tuple[str, str], CollectionRun] = {}
        for run in scoped_runs:
            runs[(run.retailer_code, run.country_code)] = run
        run_ids = list(scope.collection_run_ids)
        if not run_ids:
            return [], 0, {}
    else:
        runs = _latest_stratified_runs(session, scope)
        if not runs:
            return [], 0, {}
        run_ids = [run.id for run in runs.values()]
    seen: set[tuple[str, str, str]] = set()
    candidates: list[dict[str, Any]] = []

    snap_stmt = (
        select(ProductSnapshot, Product)
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(ProductSnapshot.collection_run_id.in_(run_ids))
    )
    snap_stmt = _apply_scope_to_product_query(snap_stmt, scope)
    snap_stmt = snap_stmt.order_by(
        ProductSnapshot.observed_at.desc(), ProductSnapshot.id.desc()
    )
    for snap, product in session.execute(snap_stmt).all():
        key = (product.retailer_code, product.country_code, product.retailer_sku)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            _candidate_from_product(
                product,
                brand=snap.brand or product.brand,
                oem=snap.oem or product.oem,
                product_type=snap.product_type or product.product_type,
                title=snap.title or product.title,
                category_raw=snap.category_raw or product.category_raw,
                availability=snap.availability,
            )
        )

    obs_stmt = (
        select(SearchObservation, Product)
        .join(Product, Product.id == SearchObservation.product_id)
        .where(SearchObservation.collection_run_id.in_(run_ids))
        .where(SearchObservation.observation_source == SOURCE_STRATIFIED_CATALOG)
        .where(SearchObservation.product_id.is_not(None))
    )
    if scope.retailer_code is not None:
        obs_stmt = obs_stmt.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        obs_stmt = obs_stmt.where(Product.country_code == scope.country_code)
    if scope.product_type is not None:
        obs_stmt = obs_stmt.where(Product.product_type == scope.product_type)
    if scope.oem is not None:
        obs_stmt = obs_stmt.where(Product.oem == scope.oem)
    if scope.brand is not None:
        obs_stmt = obs_stmt.where(Product.brand == scope.brand)
    for _obs, product in session.execute(obs_stmt).all():
        key = (product.retailer_code, product.country_code, product.retailer_sku)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(_candidate_from_product(product))

    return candidates, 0, runs


def _load_candidates_from_products(
    session: Session, scope: SosScope
) -> tuple[list[dict[str, Any]], int]:
    """Return candidate dicts and count of rows dropped by scope/inactive only."""
    stmt = select(Product).where(Product.is_active.is_(True))
    scoped = _apply_scope_to_product_query(stmt, scope)
    # Count inactive / out-of-scope separately for diagnostics
    total_active = session.scalar(
        select(func.count()).select_from(Product).where(Product.is_active.is_(True))
    ) or 0
    rows = session.scalars(scoped).all()
    scope_filtered = int(total_active) - len(rows)
    candidates = [
        {
            "product_id": p.id,
            "retailer_code": p.retailer_code,
            "country_code": p.country_code,
            "retailer_sku": p.retailer_sku,
            "brand": p.brand,
            "oem": p.oem,
            "product_type": p.product_type,
            "title": p.title,
            "category_raw": p.category_raw,
            "availability": None,
        }
        for p in rows
    ]
    return candidates, max(scope_filtered, 0)


def _load_candidates_as_of(
    session: Session, scope: SosScope
) -> tuple[list[dict[str, Any]], int]:
    """Latest snapshot per product at or before ``scope.as_of``."""
    assert scope.as_of is not None
    as_of = scope.as_of
    ranked = (
        select(
            ProductSnapshot.id.label("snap_id"),
            func.row_number()
            .over(
                partition_by=ProductSnapshot.product_id,
                order_by=(
                    ProductSnapshot.observed_at.desc(),
                    ProductSnapshot.id.desc(),
                ),
            )
            .label("rn"),
        )
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(ProductSnapshot.observed_at <= as_of)
    )
    if scope.retailer_code is not None:
        ranked = ranked.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        ranked = ranked.where(Product.country_code == scope.country_code)
    ranked_sq = ranked.subquery("ranked_snaps")

    stmt = (
        select(ProductSnapshot, Product)
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(
            ProductSnapshot.id.in_(
                select(ranked_sq.c.snap_id).where(ranked_sq.c.rn == 1)
            )
        )
    )
    if scope.product_type is not None:
        stmt = stmt.where(
            func.coalesce(ProductSnapshot.product_type, Product.product_type)
            == scope.product_type
        )
    if scope.oem is not None:
        stmt = stmt.where(func.coalesce(ProductSnapshot.oem, Product.oem) == scope.oem)
    if scope.brand is not None:
        stmt = stmt.where(
            func.coalesce(ProductSnapshot.brand, Product.brand) == scope.brand
        )

    candidates: list[dict[str, Any]] = []
    for snap, product in session.execute(stmt).all():
        candidates.append(
            {
                "product_id": product.id,
                "retailer_code": product.retailer_code,
                "country_code": product.country_code,
                "retailer_sku": product.retailer_sku,
                "brand": snap.brand or product.brand,
                "oem": snap.oem or product.oem,
                "product_type": snap.product_type or product.product_type,
                "title": snap.title or product.title,
                "category_raw": snap.category_raw or product.category_raw,
                "availability": snap.availability,
            }
        )
    return candidates, 0


def load_eligible_listings(
    session: Session,
    *,
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> tuple[list[EligibleListing], SosExclusionBreakdown, dict[tuple[str, str], CollectionRun]]:
    """Build the eligible SoS universe for the given scope from the database."""
    scope = scope or SosScope()
    cfg = config or load_sos_universe_config()
    batch_runs: dict[tuple[str, str], CollectionRun] = {}
    if scope.current_universe:
        candidates, scope_filtered, batch_runs = _load_candidates_from_current_batch(
            session, scope
        )
    elif scope.as_of is not None:
        candidates, scope_filtered = _load_candidates_as_of(session, scope)
    else:
        candidates, scope_filtered = _load_candidates_from_products(session, scope)

    eligible, raw_exclusions = build_eligible_universe(candidates, config=cfg)
    exclusions = SosExclusionBreakdown(
        accessory_or_ineligible_type=raw_exclusions.get(
            "accessory_or_ineligible_type", 0
        ),
        non_gaming=raw_exclusions.get("non_gaming", 0),
        missing_identity=raw_exclusions.get("missing_identity", 0)
        + raw_exclusions.get("duplicate_sku", 0),
        scope_filtered=scope_filtered,
        inactive=0,
    )
    return eligible, exclusions, batch_runs


def _aggregate_shares(
    listings: Sequence[EligibleListing],
    *,
    dimension: str,
) -> list[SosShare]:
    """Aggregate SoS by brand or oem. Each listing counted once."""
    if dimension not in {"brand", "oem"}:
        raise ValueError("dimension must be 'brand' or 'oem'")

    universe_size = len(listings)
    counts: dict[str, int] = {}
    for item in listings:
        raw = item.brand if dimension == "brand" else item.oem
        if raw is None or str(raw).strip() == "":
            key = "UNKNOWN"
        else:
            key = str(raw)
        # Brand path never consults oem — prevents Apple Brand+OEM double count.
        counts[key] = counts.get(key, 0) + 1

    shares = [
        SosShare(
            dimension=dimension,
            value=value,
            product_count=count,
            universe_size=universe_size,
            share=_share(count, universe_size),
        )
        for value, count in counts.items()
    ]
    shares.sort(key=lambda s: (-s.product_count, s.value))
    return shares


def share_of_shelf(
    session: Session,
    *,
    dimension: str = "brand",
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> SosSnapshot:
    """Compute Share of Shelf for ``brand`` or ``oem`` over the eligible universe."""
    scope = scope or SosScope()
    cfg = config or load_sos_universe_config()
    listings, exclusions, batch_runs = load_eligible_listings(
        session, scope=scope, config=cfg
    )
    shares = _aggregate_shares(listings, dimension=dimension)
    if scope.current_universe:
        statuses = [_completeness_from_run(run) for run in batch_runs.values()]
        collection_status = _combine_collection_status(statuses)
        run_ids = {key: int(run.id) for key, run in batch_runs.items()}
    else:
        collection_status = "COMPLETE" if listings else "NO_DATA"
        run_ids = {}
    return SosSnapshot(
        scope=scope,
        dimension=dimension,
        universe_size=len(listings),
        inclusion_rules_id=cfg.inclusion_rules_id or INCLUSION_RULES_ID,
        shares=shares,
        exclusions=exclusions,
        as_of=scope.as_of,
        collection_status=collection_status,
        collection_run_ids=run_ids,
    )


def share_of_shelf_by_brand(
    session: Session,
    *,
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> SosSnapshot:
    """Brand SoS: each product attributed once via ``brand`` (Apple counted once)."""
    return share_of_shelf(session, dimension="brand", scope=scope, config=config)


def share_of_shelf_by_oem(
    session: Session,
    *,
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> SosSnapshot:
    """OEM drilldown over the same eligible universe (separate from brand SoS)."""
    return share_of_shelf(session, dimension="oem", scope=scope, config=config)


def share_of_shelf_trends(
    session: Session,
    *,
    dimension: str = "brand",
    scope: SosScope | None = None,
    config: SosUniverseConfig | None = None,
) -> list[SosTrendPoint]:
    """Historical SoS by UTC day using latest snapshot-as-of each day end.

    For each distinct snapshot calendar day in scope, builds the eligible
    universe as-of 23:59:59.999999 UTC that day and computes shares.
    """
    scope = scope or SosScope()
    cfg = config or load_sos_universe_config()

    day_stmt = select(ProductSnapshot.observed_at).join(
        Product, Product.id == ProductSnapshot.product_id
    )
    if scope.retailer_code is not None:
        day_stmt = day_stmt.where(Product.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        day_stmt = day_stmt.where(Product.country_code == scope.country_code)
    timestamps = session.scalars(day_stmt).all()
    if not timestamps:
        return []

    days = sorted({_day_start(ts) for ts in timestamps})
    points: list[SosTrendPoint] = []
    for day in days:
        # End of UTC day
        as_of = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        day_scope = SosScope(
            retailer_code=scope.retailer_code,
            country_code=scope.country_code,
            product_type=scope.product_type,
            oem=scope.oem,
            brand=scope.brand,
            as_of=as_of,
            current_universe=False,
        )
        snap = share_of_shelf(
            session, dimension=dimension, scope=day_scope, config=cfg
        )
        points.append(
            SosTrendPoint(
                period_start=day,
                universe_size=snap.universe_size,
                shares=tuple(snap.shares),
            )
        )
    return points
