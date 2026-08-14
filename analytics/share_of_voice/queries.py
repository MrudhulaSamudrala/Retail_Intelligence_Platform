"""Share of Voice queries over append-only ``search_observations``.

Business formula (unchanged):

    SoV(brand) = brand search-result appearances
                 / total tracked-brand search-result appearances

This is **appearance / native-position based**, not unique-SKU based and not a
fixed 100-slot denominator. Tracked brands: Intel, AMD, Qualcomm, Apple.

Primary universe: ``observation_source = stratified_catalog``.
Historical ``keyword_search`` / NULL rows stay in the database and are loaded
only when ``SovScope.observation_source = keyword_search``.

Denominator inventory (stratified universe, latest SERP batch):

- observed positions: every native ``position`` kept (excluded/duplicates included)
- eligible observations: computing VALID slots (shared classifier / persist flag)
- tracked-brand observations: eligible slots whose brand is Intel/AMD/Qualcomm/Apple
  → this is the SoV denominator
- UNKNOWN observations: eligible computing slots with brand UNKNOWN (not in denom)
- OTHER observations: eligible computing slots with brand OTHER (not in denom)
- excluded observations: furniture/accessories/EXCLUDED — occupy native position,
  not in tracked denom
- duplicate observations: repeated SKU in the same stratum/query — native position
  kept; not a second unique SKU; DUPLICATE slots are not SoV appearances
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics.share_of_voice.models import (
    SOV_SOURCE_KEYWORD_SEARCH,
    SOV_SOURCE_STRATIFIED_CATALOG,
    BrandKeywordMetrics,
    SovScope,
    SovSnapshot,
    SovTrendPoint,
)
from collector.search.config import load_sov_config
from collector.search.persist import is_stratified_catalog_observation
from database.models import SearchObservation

TRACKED_DEFAULT = ("Intel", "AMD", "Qualcomm", "Apple")
UNKNOWN = "UNKNOWN"
OTHER = "OTHER"


def _share(count: int, total: int) -> Decimal:
    if total <= 0:
        return Decimal("0")
    return (Decimal(count) / Decimal(total)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


def _avg(values: Iterable[int]) -> Optional[Decimal]:
    items = list(values)
    if not items:
        return None
    return (Decimal(sum(items)) / Decimal(len(items))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _day_start(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _source_for(scope: SovScope) -> str:
    raw = (scope.observation_source or SOV_SOURCE_STRATIFIED_CATALOG).strip()
    if raw == SOV_SOURCE_KEYWORD_SEARCH:
        return SOV_SOURCE_KEYWORD_SEARCH
    return SOV_SOURCE_STRATIFIED_CATALOG


def _is_stratified_scope(scope: SovScope) -> bool:
    return _source_for(scope) == SOV_SOURCE_STRATIFIED_CATALOG


def _details(row: SearchObservation) -> dict:
    return row.details if isinstance(row.details, dict) else {}


def _is_duplicate_obs(row: SearchObservation) -> bool:
    details = _details(row)
    return bool(
        details.get("duplicate") or details.get("slot_status") == "DUPLICATE"
    )


def _is_excluded_obs(row: SearchObservation) -> bool:
    details = _details(row)
    return bool(
        details.get("excluded") or details.get("slot_status") == "EXCLUDED"
    )


def _used_fallback(row: SearchObservation) -> bool:
    return bool(_details(row).get("used_fallback"))


def _stratum_name(row: SearchObservation) -> str:
    return str(row.stratum or _details(row).get("stratum") or row.keyword or "")


def _batch_key(row: SearchObservation, *, stratified: bool) -> tuple[str, str, str]:
    if stratified and _stratum_name(row):
        return (row.retailer_code, row.country_code, _stratum_name(row))
    return (row.retailer_code, row.country_code, row.keyword)


def _load_rows(session: Session, scope: SovScope) -> Sequence[SearchObservation]:
    cfg = load_sov_config()
    source = _source_for(scope)
    stratified = source == SOV_SOURCE_STRATIFIED_CATALOG
    stmt = select(SearchObservation)
    if scope.retailer_code is not None:
        stmt = stmt.where(SearchObservation.retailer_code == scope.retailer_code)
    if scope.country_code is not None:
        stmt = stmt.where(SearchObservation.country_code == scope.country_code)
    if scope.keyword is not None:
        stmt = stmt.where(SearchObservation.keyword == scope.keyword)
    if scope.stratum is not None:
        stmt = stmt.where(SearchObservation.stratum == scope.stratum)
    if scope.observed_from is not None:
        stmt = stmt.where(SearchObservation.observed_at >= scope.observed_from)
    if scope.observed_to is not None:
        stmt = stmt.where(SearchObservation.observed_at <= scope.observed_to)
    # Keyword-search completeness can be filtered in SQL. Stratified completeness
    # is evaluated per configured budget / fallback after load.
    if scope.require_complete and not stratified:
        stmt = stmt.where(SearchObservation.collection_status == "COMPLETE")
    organic_only = (
        cfg.prefer_organic if scope.organic_only is None else scope.organic_only
    )
    if organic_only:
        stmt = stmt.where(SearchObservation.is_sponsored.is_(False))
    stmt = stmt.order_by(
        SearchObservation.observed_at.asc(),
        SearchObservation.position.asc(),
        SearchObservation.id.asc(),
    )
    loaded = list(session.scalars(stmt).all())
    if stratified:
        return [row for row in loaded if is_stratified_catalog_observation(row)]
    return [row for row in loaded if not is_stratified_catalog_observation(row)]


def _observation_is_eligible(row: SearchObservation) -> bool:
    """Computing-eligible slot for brand attribution (not tracked-brand assignment).

    Excluded furniture/accessories and in-stratum SKU duplicates stay in the SERP
    record but are not SoV appearances. UNKNOWN/OTHER brands on VALID products
    remain UNKNOWN/OTHER.
    """
    details = _details(row)
    if _is_duplicate_obs(row) or _is_excluded_obs(row):
        return False
    slot_status = details.get("slot_status") or details.get("extraction_status")
    if slot_status in {"FAILED", "INACCESSIBLE", "BLOCKED"}:
        return False
    if "is_eligible" in details:
        return bool(details.get("is_eligible"))
    # Historical keyword rows: ML junk is classified; other retailers stay eligible
    # unless persist already flagged them.
    if getattr(row, "retailer_code", None) == "mercadolibre":
        from collector.retailers.mercadolibre.classification import (
            classify_mercadolibre_product,
            is_collection_eligible,
        )

        return is_collection_eligible(
            classify_mercadolibre_product(title=getattr(row, "title", None))
        )
    return True


def _dedupe_latest_search(
    rows: Sequence[SearchObservation],
    *,
    stratified: bool = False,
    include_ineligible: bool = False,
) -> list[SearchObservation]:
    """Keep one observation per (retailer, country, keyword/stratum, position)
    from the latest search batch (by observed_at).

    Native ``position`` is never renumbered. Ineligible hits are dropped from
    brand attribution unless ``include_ineligible`` (stratified SERP inventory).
    """
    if not rows:
        return []
    latest: dict[tuple[str, str, str], datetime] = {}
    for r in rows:
        key = _batch_key(r, stratified=stratified)
        prev = latest.get(key)
        if prev is None or r.observed_at > prev:
            latest[key] = r.observed_at

    filtered = [
        r
        for r in rows
        if r.observed_at == latest[_batch_key(r, stratified=stratified)]
    ]
    if not include_ineligible:
        filtered = [r for r in filtered if _observation_is_eligible(r)]
    seen: set[tuple[str, str, str, int]] = set()
    out: list[SearchObservation] = []
    for r in filtered:
        slot = (*_batch_key(r, stratified=stratified), int(r.position))
        if slot in seen:
            continue
        seen.add(slot)
        out.append(r)
    return out


def _stratum_budget(retailer_code: str, stratum: str) -> int:
    from collector.universe_config import STRATUM_BUDGETS, strata_for

    for spec in strata_for(retailer_code):
        if spec.name == stratum:
            return int(spec.budget)
    return int(STRATUM_BUDGETS.get(stratum, 0))


def _configured_stratum_names(retailer_code: str) -> tuple[str, ...]:
    from collector.universe_config import STRATUM_ORDER, strata_for

    specs = strata_for(retailer_code)
    if specs:
        return tuple(spec.name for spec in specs)
    return STRATUM_ORDER


def _evaluate_stratum_status(
    rows: Sequence[SearchObservation],
    *,
    retailer_code: str,
    stratum: str,
) -> str:
    """COMPLETE only when the configured native-position budget was observed
    without ofertas/fallback. Missing positions or fallback → PARTIAL.
    """
    budget = _stratum_budget(retailer_code, stratum)
    if not rows:
        return "PARTIAL"
    positions = {int(r.position) for r in rows}
    observed = len(positions)
    if any(_used_fallback(r) for r in rows):
        return "PARTIAL"
    statuses = {str(r.collection_status or "") for r in rows}
    if observed <= 0:
        if "BLOCKED" in statuses:
            return "BLOCKED"
        if "FAILED" in statuses:
            return "FAILED"
        return "PARTIAL"
    if (
        budget > 0
        and observed >= budget
        and statuses <= {"COMPLETE"}
        and "PARTIAL" not in statuses
        and "BLOCKED" not in statuses
        and "FAILED" not in statuses
    ):
        return "COMPLETE"
    return "PARTIAL"


def _stratum_status_map(
    serp_rows: Sequence[SearchObservation],
    scope: SovScope,
) -> dict[str, str]:
    if not serp_rows and not scope.retailer_code:
        return {}
    retailers = (
        [scope.retailer_code]
        if scope.retailer_code
        else sorted({r.retailer_code for r in serp_rows})
    )
    if not retailers:
        return {}
    grouped: dict[tuple[str, str], list[SearchObservation]] = defaultdict(list)
    for row in serp_rows:
        grouped[(row.retailer_code, _stratum_name(row))].append(row)
    status: dict[str, str] = {}
    names = (
        (scope.stratum,)
        if scope.stratum
        else tuple(
            name
            for retailer in retailers
            for name in _configured_stratum_names(retailer)
        )
    )
    # Unique stratum names in configured order (first retailer).
    ordered: list[str] = []
    for name in names:
        if name and name not in ordered:
            ordered.append(name)
    primary = retailers[0]
    for name in ordered:
        bucket: list[SearchObservation] = []
        for retailer in retailers:
            bucket.extend(grouped.get((retailer, name), []))
        retailer_for_budget = primary
        if bucket:
            retailer_for_budget = bucket[0].retailer_code
        status[name] = _evaluate_stratum_status(
            bucket, retailer_code=retailer_for_budget, stratum=name
        )
    return status


def _basis_for_rows(rows: Sequence[SearchObservation]) -> str:
    statuses = {r.collection_status for r in rows}
    if not statuses:
        return "empty"
    if statuses == {"COMPLETE"}:
        return "exact"
    if "PARTIAL" in statuses or "FAILED" in statuses:
        if "COMPLETE" in statuses:
            return "mixed"
        return "observed_partial"
    if statuses == {"ZERO_RESULTS"}:
        return "empty"
    return "mixed"


def _basis_from_stratum_status(status_map: dict[str, str]) -> str:
    if not status_map:
        return "empty"
    values = set(status_map.values())
    if values == {"COMPLETE"}:
        return "exact"
    return "observed_partial"


def _unique_tracked_skus(
    eligible_rows: Sequence[SearchObservation],
    tracked: set[str],
) -> int:
    keys: set[tuple[str, str, str, str]] = set()
    for row in eligible_rows:
        if row.brand not in tracked:
            continue
        sku = (row.retailer_sku or "").strip()
        if not sku:
            continue
        keys.add((row.retailer_code, row.country_code, _stratum_name(row), sku))
    return len(keys)


def _metrics_from_eligible_rows(
    eligible_rows: Sequence[SearchObservation],
    *,
    scope: SovScope,
    top_n: int,
    collection_basis: str,
    stratum: str | None = None,
) -> list[BrandKeywordMetrics]:
    cfg = load_sov_config()
    tracked = set(cfg.tracked_brands)
    include_unknown = cfg.include_unknown_in_denominator

    def is_denom(brand: Optional[str]) -> bool:
        if brand in tracked:
            return True
        return bool(include_unknown and (brand is None or brand == UNKNOWN))

    denom_rows = [r for r in eligible_rows if is_denom(r.brand)]
    total_tracked = len(denom_rows)

    by_brand: dict[str, list[SearchObservation]] = defaultdict(list)
    for r in eligible_rows:
        brand = r.brand or UNKNOWN
        if brand in tracked or brand == UNKNOWN:
            by_brand[brand].append(r)

    keyword = scope.keyword
    if stratum and eligible_rows:
        keyword = eligible_rows[0].keyword or keyword
    elif stratum and not keyword:
        from collector.universe_config import strata_for

        retailer = scope.retailer_code or (
            eligible_rows[0].retailer_code if eligible_rows else "newegg"
        )
        for spec in strata_for(retailer):
            if spec.name == stratum:
                keyword = spec.query
                break

    metrics: list[BrandKeywordMetrics] = []
    for brand in list(cfg.tracked_brands) + ([UNKNOWN] if UNKNOWN in by_brand else []):
        brand_rows = by_brand.get(brand, [])
        appearances = len(brand_rows)
        top_count = sum(1 for r in brand_rows if int(r.position) <= top_n)
        ranks = [int(r.position) for r in brand_rows]
        if brand == UNKNOWN and not include_unknown:
            share = Decimal("0")
        elif brand in tracked or include_unknown:
            share = _share(appearances, total_tracked)
        else:
            share = Decimal("0")
        metrics.append(
            BrandKeywordMetrics(
                brand=brand,
                keyword=keyword,
                retailer_code=scope.retailer_code,
                country_code=scope.country_code,
                present=appearances > 0,
                appearances=appearances,
                top_n_count=top_count,
                top_n=top_n,
                average_rank=_avg(ranks),
                rank_observation_count=len(ranks),
                share_of_voice=share,
                total_tracked_appearances=total_tracked,
                collection_basis=collection_basis,
                stratum=stratum,
            )
        )
    return metrics


def _latest_for_scope(
    session: Session, scope: SovScope
) -> tuple[list[SearchObservation], list[SearchObservation]]:
    stratified = _is_stratified_scope(scope)
    raw = _load_rows(session, scope)
    serp = _dedupe_latest_search(
        raw, stratified=stratified, include_ineligible=stratified
    )
    if stratified and scope.require_complete:
        status_map = _stratum_status_map(serp, scope)
        complete = {name for name, status in status_map.items() if status == "COMPLETE"}
        serp = [row for row in serp if _stratum_name(row) in complete]
    eligible = [row for row in serp if _observation_is_eligible(row)]
    return serp, eligible


def brand_presence(
    session: Session,
    *,
    scope: SovScope | None = None,
) -> dict[str, bool]:
    scope = scope or SovScope()
    cfg = load_sov_config()
    _serp, eligible = _latest_for_scope(session, scope)
    present = {b: False for b in cfg.tracked_brands}
    for r in eligible:
        if r.brand in present:
            present[r.brand] = True
    return present


def keyword_metrics(
    session: Session,
    *,
    scope: SovScope | None = None,
    top_n: int | None = None,
) -> list[BrandKeywordMetrics]:
    """Per-brand metrics for a scoped result set (optionally one keyword/stratum)."""
    scope = scope or SovScope()
    cfg = load_sov_config()
    n = int(top_n or scope.top_n or cfg.default_top_n)
    serp, eligible = _latest_for_scope(session, scope)
    if _is_stratified_scope(scope):
        status_map = _stratum_status_map(serp, scope)
        basis = _basis_from_stratum_status(status_map) if status_map else "empty"
        if not serp:
            basis = "empty"
    else:
        basis = _basis_for_rows(eligible)
    return _metrics_from_eligible_rows(
        eligible,
        scope=scope,
        top_n=n,
        collection_basis=basis,
        stratum=scope.stratum,
    )


def share_of_voice(
    session: Session,
    *,
    scope: SovScope | None = None,
    top_n: int | None = None,
) -> SovSnapshot:
    scope = scope or SovScope()
    cfg = load_sov_config()
    tracked = set(cfg.tracked_brands)
    n = int(top_n or scope.top_n or cfg.default_top_n)
    source = _source_for(scope)
    serp, eligible = _latest_for_scope(session, scope)

    if source == SOV_SOURCE_STRATIFIED_CATALOG:
        status_map = _stratum_status_map(serp, scope)
        if not serp and not status_map:
            basis = "empty"
        else:
            basis = _basis_from_stratum_status(status_map)
        complete = sum(1 for s in status_map.values() if s == "COMPLETE")
        partial = sum(1 for s in status_map.values() if s == "PARTIAL")
        failed = sum(1 for s in status_map.values() if s in {"FAILED", "BLOCKED"})
        overall_metrics = _metrics_from_eligible_rows(
            eligible, scope=scope, top_n=n, collection_basis=basis, stratum=None
        )
        stratum_metrics: list[BrandKeywordMetrics] = []
        by_stratum: dict[str, list[SearchObservation]] = defaultdict(list)
        eligible_by_stratum: dict[str, list[SearchObservation]] = defaultdict(list)
        for row in serp:
            by_stratum[_stratum_name(row)].append(row)
        for row in eligible:
            eligible_by_stratum[_stratum_name(row)].append(row)
        for name in status_map:
            stratum_basis = (
                "exact" if status_map[name] == "COMPLETE" else "observed_partial"
            )
            stratum_metrics.extend(
                _metrics_from_eligible_rows(
                    eligible_by_stratum.get(name, []),
                    scope=scope,
                    top_n=n,
                    collection_basis=stratum_basis,
                    stratum=name,
                )
            )
    else:
        status_map = {}
        basis = _basis_for_rows(eligible)
        run_keys: dict[tuple[str, str, str], str] = {}
        for r in eligible:
            key = (r.retailer_code, r.country_code, r.keyword)
            run_keys[key] = r.collection_status
        complete = sum(1 for s in run_keys.values() if s == "COMPLETE")
        partial = sum(1 for s in run_keys.values() if s == "PARTIAL")
        failed = sum(1 for s in run_keys.values() if s == "FAILED")
        overall_metrics = _metrics_from_eligible_rows(
            eligible, scope=scope, top_n=n, collection_basis=basis, stratum=None
        )
        stratum_metrics = []

    unknown = sum(1 for r in eligible if (r.brand or UNKNOWN) == UNKNOWN)
    other = sum(1 for r in eligible if r.brand == OTHER)
    tracked_count = sum(1 for r in eligible if r.brand in tracked)
    excluded = sum(1 for r in serp if _is_excluded_obs(r))
    duplicates = sum(1 for r in serp if _is_duplicate_obs(r))

    return SovSnapshot(
        scope=scope,
        total_observations=len(serp),
        tracked_appearances=tracked_count,
        unknown_appearances=unknown,
        complete_searches=complete,
        partial_searches=partial,
        failed_searches=failed,
        metrics=[m for m in overall_metrics if m.brand in tracked],
        collection_basis=basis,
        observation_source=source,
        eligible_observations=len(eligible),
        other_appearances=other,
        excluded_observations=excluded,
        duplicate_observations=duplicates,
        unique_tracked_skus=_unique_tracked_skus(eligible, tracked),
        stratum_metrics=[m for m in stratum_metrics if m.brand in tracked],
        stratum_status=status_map,
    )


def share_of_voice_trends(
    session: Session,
    *,
    scope: SovScope | None = None,
    top_n: int | None = None,
) -> list[SovTrendPoint]:
    scope = scope or SovScope()
    cfg = load_sov_config()
    n = int(top_n or scope.top_n or cfg.default_top_n)
    stratified = _is_stratified_scope(scope)
    rows = list(_load_rows(session, scope))
    if not rows:
        return []

    buckets: dict[tuple[datetime, str, str, str], list[SearchObservation]] = defaultdict(
        list
    )
    for r in rows:
        key = (
            _day_start(r.observed_at),
            r.retailer_code,
            r.country_code,
            _stratum_name(r) if stratified else r.keyword,
        )
        buckets[key].append(r)

    points: list[SovTrendPoint] = []
    tracked = set(cfg.tracked_brands)
    for (day, retailer, country, group), bucket in sorted(buckets.items()):
        seen: set[int] = set()
        unique: list[SearchObservation] = []
        for r in sorted(bucket, key=lambda x: (x.position, x.id)):
            if r.position in seen:
                continue
            seen.add(int(r.position))
            unique.append(r)
        eligible = [r for r in unique if _observation_is_eligible(r)]
        denom = [
            r
            for r in eligible
            if r.brand in tracked
            or (
                cfg.include_unknown_in_denominator
                and (r.brand is None or r.brand == UNKNOWN)
            )
        ]
        total = len(denom)
        sample = unique[0] if unique else None
        keyword = sample.keyword if sample is not None else group
        stratum = _stratum_name(sample) if stratified and sample is not None else None
        for brand in cfg.tracked_brands:
            brand_rows = [r for r in eligible if r.brand == brand]
            appearances = len(brand_rows)
            top_count = sum(1 for r in brand_rows if int(r.position) <= n)
            ranks = [int(r.position) for r in brand_rows]
            points.append(
                SovTrendPoint(
                    period_start=day,
                    retailer_code=retailer,
                    country_code=country,
                    keyword=keyword,
                    brand=brand,
                    appearances=appearances,
                    top_n_count=top_count,
                    average_rank=_avg(ranks),
                    share_of_voice=_share(appearances, total),
                    total_tracked_appearances=total,
                    stratum=stratum,
                )
            )
    return points
