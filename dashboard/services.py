"""Dashboard data-access / analytics service layer.

All metrics flow: dashboard → existing analytics → PostgreSQL.
Caching is short-lived and invalidated by Refresh Analytics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from analytics import (
    PricingScope,
    average_discount,
    average_price_by_brand,
    banner_share_by_brand,
    compare_by_country,
    compare_by_product_type,
    compare_by_retailer,
    compute_brand_scores,
    compute_compliance_score,
    compute_country_scores,
    compute_retailer_scores,
    count_discounted_products,
    discount_change_over_time,
    highest_cross_retailer_visibility,
    highest_visibility_by_retailer,
    keyword_metrics,
    list_cross_retailer_visibility,
    list_price_observations,
    list_product_visibility,
    load_compliance_score_config,
    median_price_by_brand,
    price_change_over_time,
    share_of_shelf_by_brand,
    share_of_shelf_by_oem,
    share_of_shelf_trends,
    share_of_voice,
    share_of_voice_trends,
)
from analytics.compliance.queries import load_audit_rows
from analytics.product_identity import MATCHED as MATCHED_STATUS
from analytics.product_identity.queries import crosswalk_summary, retailer_product_counts

from dashboard.config import alert_thresholds
from dashboard.filters import (
    DashboardFilters,
    audit_row_matches,
    previous_period,
    to_banner_scope,
    to_pricing_scope,
    to_sos_scope,
    to_sov_scope,
    to_visibility_scope,
)
from dashboard.queries.catalog import list_sku_rows, product_detail
from dashboard.queries.collection import (
    CollectionStatusSnapshot,
    count_tracked_products,
    filter_option_values,
    load_collection_status,
)
from dashboard.presentation import CoverageDisplay, format_search_coverage
from dashboard.utils.format import fmt_money, fmt_pct
from dashboard.utils.semantics import DataState, MetricValue


@dataclass
class Insight:
    category: str
    text: str
    metric: str
    entity: str
    current_value: Optional[str]
    previous_value: Optional[str]
    change: Optional[str]
    timestamp: datetime
    source: str
    detail_key: Optional[str] = None


@dataclass
class AlertItem:
    severity: str  # info | warning | critical
    title: str
    detail: str
    source: str


def _avg_price_overall(session: Session, scope: PricingScope) -> tuple[Optional[Decimal], Optional[str], int]:
    obs = list_price_observations(session, scope=scope, latest_only=True)
    priced = [o for o in obs if o.current_price is not None]
    if not priced:
        return None, None, 0
    currencies = {o.currency for o in priced}
    if len(currencies) > 1:
        # Do not mix currencies — return None with multi-currency signal
        return None, "MIXED", len(priced)
    currency = next(iter(currencies))
    total = sum((o.current_price for o in priced), Decimal("0"))
    return total / Decimal(len(priced)), currency, len(priced)


def _median_price_overall(session: Session, scope: PricingScope) -> tuple[Optional[Decimal], Optional[str], int]:
    obs = list_price_observations(session, scope=scope, latest_only=True)
    priced = [o for o in obs if o.current_price is not None]
    if not priced:
        return None, None, 0
    currencies = {o.currency for o in priced}
    if len(currencies) > 1:
        return None, "MIXED", len(priced)
    currency = next(iter(currencies))
    prices = sorted(o.current_price for o in priced if o.current_price is not None)
    n = len(prices)
    mid = n // 2
    if n % 2:
        med = prices[mid]
    else:
        med = (prices[mid - 1] + prices[mid]) / Decimal(2)
    return med, currency, n


def _count_price_changes(session: Session, filters: DashboardFilters) -> int:
    """Distinct products whose latest two observations differ in price."""
    scope = to_pricing_scope(filters)
    obs = list_price_observations(session, scope=scope, latest_only=False)
    by_product: dict[int, list] = {}
    for o in obs:
        by_product.setdefault(o.product_id, []).append(o)
    changed = 0
    for rows in by_product.values():
        rows_sorted = sorted(rows, key=lambda r: r.observed_at, reverse=True)
        if len(rows_sorted) < 2:
            continue
        a, b = rows_sorted[0].current_price, rows_sorted[1].current_price
        if a is None or b is None:
            continue
        if a != b:
            changed += 1
    return changed


def _compliance_for_filters(session: Session, filters: DashboardFilters):
    """Current-universe audits. Date range is not part of the compliance loader."""
    rows = [
        r
        for r in load_audit_rows(session, current_universe=True)
        if audit_row_matches(r, filters)
    ]
    cfg = load_compliance_score_config()
    score = compute_compliance_score(rows, config=cfg)
    return rows, score


def metric_tracked_products(session: Session, filters: DashboardFilters) -> MetricValue:
    n = count_tracked_products(session, retailer_code=filters.retailer_code)
    # Apply additional filters via SKU list for accurate scoped count
    if any([filters.country_code, filters.product_type, filters.brand, filters.oem]):
        n = len(list_sku_rows(session, filters=filters, limit=100_000))
    return MetricValue.from_number(
        n,
        display=str(n),
        denominator=n,
        numerator=n,
        source="database.products",
        definition="Count of active tracked products matching filters",
    )


def metric_share_of_shelf(session: Session, filters: DashboardFilters) -> tuple[MetricValue, Any]:
    snap = share_of_shelf_by_brand(session, scope=to_sos_scope(filters))
    if snap.universe_size <= 0:
        return (
            MetricValue.from_number(
                None,
                source="analytics.share_of_shelf",
                definition="Brand product count / eligible tracked universe (sos_universe_v1)",
                detail="No eligible products in SoS universe for selected filters",
            ),
            snap,
        )
    top = snap.shares[0] if snap.shares else None
    if top is None:
        return (
            MetricValue.from_number(
                0,
                display="0%",
                denominator=snap.universe_size,
                numerator=0,
                source="analytics.share_of_shelf",
                definition="Brand product count / eligible tracked universe (sos_universe_v1)",
            ),
            snap,
        )
    return (
        MetricValue.from_number(
            float(top.share),
            display=f"{top.value}: {fmt_pct(top.share)}",
            denominator=snap.universe_size,
            numerator=top.product_count,
            source="analytics.share_of_shelf",
            definition="Brand product count / eligible tracked universe (sos_universe_v1)",
            detail=f"Universe={snap.universe_size}; rules={snap.inclusion_rules_id}",
        ),
        snap,
    )


def metric_average_price(session: Session, filters: DashboardFilters) -> MetricValue:
    avg, currency, n = _avg_price_overall(session, to_pricing_scope(filters))
    if currency == "MIXED":
        return MetricValue(
            state=DataState.INSUFFICIENT,
            display="Multiple currencies",
            detail="Filter to a single retailer/country to avoid mixing USD and BRL",
            denominator=n,
            source="analytics.pricing",
            definition="Mean of latest current_price observations",
        )
    return MetricValue.from_number(
        float(avg) if avg is not None else None,
        display=fmt_money(avg, currency),
        denominator=n,
        source="analytics.pricing",
        definition="Mean of latest current_price observations (native currency)",
    )


def metric_average_discount(session: Session, filters: DashboardFilters) -> MetricValue:
    scope = to_pricing_scope(filters)
    avg = average_discount(session, scope=scope)
    discounted = count_discounted_products(session, scope=scope)
    obs = list_price_observations(session, scope=scope, latest_only=True)
    with_disc = [o for o in obs if o.discount_pct is not None]
    if avg is None:
        return MetricValue.from_number(
            None,
            detail="No products with valid discount_pct observations",
            denominator=len(obs),
            numerator=0,
            source="analytics.pricing.average_discount",
            definition="Mean discount_pct among latest observations that have discount_pct",
        )
    return MetricValue.from_number(
        float(avg),
        display=fmt_pct(avg, already_ratio=False),
        denominator=len(with_disc),
        numerator=discounted,
        source="analytics.pricing.average_discount",
        definition="Mean discount_pct among latest observations that have discount_pct",
        detail=f"Discounted products={discounted}",
    )


def metric_compliance(session: Session, filters: DashboardFilters) -> tuple[MetricValue, Any]:
    rows, score = _compliance_for_filters(session, filters)
    if score.overall_score is None:
        detail = "Insufficient notebook/desktop scored audits for weighted overall"
        return (
            MetricValue.from_number(
                None,
                detail=detail,
                source="analytics.compliance.compute_compliance_score",
                definition="overall = notebook×0.85 + desktop×0.15; UNKNOWN excluded from pass rate",
            ),
            score,
        )
    return (
        MetricValue.from_number(
            score.overall_score,
            display=fmt_pct(score.overall_score),
            denominator=score.coverage.scored_count,
            numerator=score.coverage.pass_count,
            source="analytics.compliance.compute_compliance_score",
            definition="overall = notebook×0.85 + desktop×0.15; UNKNOWN ≠ FAIL",
            detail=(
                f"PASS={score.coverage.pass_count} FAIL={score.coverage.fail_count} "
                f"UNKNOWN={score.coverage.unknown_count}"
            ),
        ),
        score,
    )


def metric_banner_share(session: Session, filters: DashboardFilters) -> tuple[MetricValue, Any]:
    snap = banner_share_by_brand(session, scope=to_banner_scope(filters))
    if snap.total_tracked_banners <= 0:
        return (
            MetricValue.from_number(
                None,
                detail="No tracked homepage banner observations",
                source="analytics.banner_share",
                definition="Tracked brand banner count / tracked banner denominator",
            ),
            snap,
        )
    top = snap.shares[0] if snap.shares else None
    if top is None:
        return (
            MetricValue.from_number(
                0,
                display="0%",
                denominator=snap.total_tracked_banners,
                source="analytics.banner_share",
                definition="Tracked brand banner count / tracked banner denominator",
            ),
            snap,
        )
    return (
        MetricValue.from_number(
            float(top.banner_share),
            display=f"{top.brand}: {fmt_pct(top.banner_share)}",
            denominator=snap.total_tracked_banners,
            numerator=top.banner_count,
            source="analytics.banner_share",
            definition="Tracked brand banner count / tracked banner denominator",
        ),
        snap,
    )


def metric_share_of_voice(session: Session, filters: DashboardFilters) -> tuple[MetricValue, Any]:
    snap = share_of_voice(session, scope=to_sov_scope(filters))
    partial = snap.partial_searches > 0 or snap.collection_basis in {
        "observed_partial",
        "mixed",
    }
    if snap.tracked_appearances <= 0:
        mv = MetricValue.from_number(
            None,
            detail="No tracked search appearances in scope",
            source="analytics.share_of_voice",
            definition="Brand appearances / tracked appearances (organic preferred)",
        )
        if partial:
            mv = MetricValue.partial(
                "PARTIAL — no tracked appearances",
                detail="Partial search coverage",
                source="analytics.share_of_voice",
            )
        return mv, snap
    top = max(snap.metrics, key=lambda m: m.share_of_voice) if snap.metrics else None
    display = f"{top.brand}: {fmt_pct(top.share_of_voice)}" if top else "No data"
    state_detail = ""
    if partial:
        state_detail = (
            "Partial search coverage — this is not an exact full-SERP Share of Voice measurement."
        )
    mv = MetricValue.from_number(
        float(top.share_of_voice) if top else None,
        display=display,
        denominator=snap.tracked_appearances,
        numerator=top.appearances if top else None,
        source="analytics.share_of_voice",
        definition="Brand appearances / tracked appearances",
        detail=state_detail or f"basis={snap.collection_basis}",
    )
    if partial and top is not None:
        # Keep numeric value but mark PARTIAL semantics via detail/state overlay
        mv = MetricValue(
            state=DataState.PARTIAL,
            value=float(top.share_of_voice),
            display=display,
            detail=state_detail,
            denominator=snap.tracked_appearances,
            numerator=top.appearances,
            source="analytics.share_of_voice",
            definition="Brand appearances / tracked appearances",
        )
    return mv, snap


def build_alerts(
    session: Session,
    filters: DashboardFilters,
    *,
    collection: CollectionStatusSnapshot,
    compliance_score,
    sos_snap,
    sov_snap,
) -> list[AlertItem]:
    thr = alert_thresholds()
    alerts: list[AlertItem] = []

    if collection.is_partial:
        alerts.append(
            AlertItem(
                "warning",
                "PARTIAL DATA",
                "Latest collection completed with PARTIAL status. Metrics may under-represent reality.",
                "collection_runs",
            )
        )
    if collection.is_stale:
        alerts.append(
            AlertItem(
                "warning",
                "Stale collection data",
                f"Last successful collection exceeds {thr.get('collection_stale_hours', 12)}h threshold.",
                "collection_runs",
            )
        )
    if collection.latest_status in {"FAILED", "ERROR"}:
        alerts.append(
            AlertItem(
                "critical",
                "Collection failure",
                f"Latest run status={collection.latest_status}",
                "collection_runs",
            )
        )
    for comp in collection.components:
        if comp.status == "PARTIAL":
            alerts.append(
                AlertItem(
                    "warning",
                    f"{comp.component} PARTIAL",
                    comp.reason or "Component reported PARTIAL",
                    "collection_run_steps",
                )
            )
        elif comp.status == "FAILED":
            alerts.append(
                AlertItem(
                    "critical",
                    f"{comp.component} FAILED",
                    comp.reason or "Component failed",
                    "collection_run_steps",
                )
            )

    if compliance_score is not None and compliance_score.overall_score is not None:
        if compliance_score.overall_score < thr.get("compliance_score_below", 0.7):
            alerts.append(
                AlertItem(
                    "critical",
                    "Compliance below threshold",
                    f"Overall score {fmt_pct(compliance_score.overall_score)} "
                    f"< {fmt_pct(thr.get('compliance_score_below', 0.7))}",
                    "analytics.compliance",
                )
            )

    if sov_snap is not None and (
        sov_snap.partial_searches > 0 or sov_snap.collection_basis != "exact"
    ):
        alerts.append(
            AlertItem(
                "warning",
                "Search visibility PARTIAL",
                "Partial search coverage — not an exact full-SERP Share of Voice measurement.",
                "analytics.share_of_voice",
            )
        )

    # SoS period decline
    prev = previous_period(filters)
    if prev is not None and sos_snap is not None and sos_snap.shares:
        prev_snap = share_of_shelf_by_brand(session, scope=to_sos_scope(prev))
        thr_pp = thr.get("sos_share_decline_pp", 3.0)
        prev_map = {s.value: float(s.share) for s in prev_snap.shares}
        for s in sos_snap.shares:
            old = prev_map.get(s.value)
            if old is None:
                continue
            delta_pp = (float(s.share) - old) * 100.0
            if delta_pp <= -thr_pp:
                alerts.append(
                    AlertItem(
                        "warning",
                        f"{s.value} Share of Shelf decline",
                        f"{delta_pp:.1f} pp vs previous comparable period",
                        "analytics.share_of_shelf",
                    )
                )

    return alerts


def build_insights(session: Session, filters: DashboardFilters) -> list[Insight]:
    now = datetime.now(timezone.utc)
    insights: list[Insight] = []
    prev = previous_period(filters)

    sos = share_of_shelf_by_brand(session, scope=to_sos_scope(filters))
    if sos.shares and prev is not None:
        prev_sos = share_of_shelf_by_brand(session, scope=to_sos_scope(prev))
        prev_map = {s.value: float(s.share) for s in prev_sos.shares}
        for s in sos.shares:
            old = prev_map.get(s.value)
            if old is None:
                continue
            delta_pp = (float(s.share) - old) * 100.0
            if abs(delta_pp) >= 0.5:
                verb = "gained" if delta_pp > 0 else "lost"
                insights.append(
                    Insight(
                        category="Share of Shelf",
                        text=f"{s.value} {verb} {abs(delta_pp):.1f} percentage points of Share of Shelf.",
                        metric="share_of_shelf",
                        entity=s.value,
                        current_value=fmt_pct(s.share),
                        previous_value=fmt_pct(old),
                        change=f"{delta_pp:+.1f} pp",
                        timestamp=now,
                        source="analytics.share_of_shelf_by_brand",
                        detail_key=f"sos:{s.value}",
                    )
                )

    brand_prices = average_price_by_brand(session, scope=to_pricing_scope(filters))
    if brand_prices:
        # Highest average within each currency separately
        by_ccy: dict[str, list] = {}
        for row in brand_prices:
            if row.average_price is None:
                continue
            by_ccy.setdefault(row.currency, []).append(row)
        for ccy, rows in by_ccy.items():
            top = max(rows, key=lambda r: r.average_price or Decimal(0))
            insights.append(
                Insight(
                    category="Pricing",
                    text=f"{top.value} has the highest average price ({fmt_money(top.average_price, ccy)}).",
                    metric="average_price",
                    entity=top.value,
                    current_value=fmt_money(top.average_price, ccy),
                    previous_value=None,
                    change=None,
                    timestamp=now,
                    source="analytics.pricing.average_price_by_brand",
                )
            )
            disc_rows = [r for r in rows if r.average_discount_pct is not None]
            if disc_rows:
                dtop = max(disc_rows, key=lambda r: r.average_discount_pct or Decimal(0))
                insights.append(
                    Insight(
                        category="Promotions",
                        text=(
                            f"{dtop.value} has the highest average discount "
                            f"({fmt_pct(dtop.average_discount_pct, already_ratio=False)})."
                        ),
                        metric="average_discount",
                        entity=dtop.value,
                        current_value=str(dtop.average_discount_pct),
                        previous_value=None,
                        change=None,
                        timestamp=now,
                        source="analytics.pricing.average_price_by_brand",
                    )
                )

    changed = _count_price_changes(session, filters)
    if changed:
        insights.append(
            Insight(
                category="Pricing",
                text=f"{changed} products experienced a price change (latest vs prior observation).",
                metric="price_changes",
                entity="products",
                current_value=str(changed),
                previous_value=None,
                change=None,
                timestamp=now,
                source="analytics.pricing.list_price_observations",
            )
        )

    sov = share_of_voice(session, scope=to_sov_scope(filters))
    if sov.partial_searches > 0 or sov.collection_basis != "exact":
        insights.append(
            Insight(
                category="Visibility",
                text="Search visibility / Share of Voice is PARTIAL for the selected scope.",
                metric="share_of_voice",
                entity="search",
                current_value=sov.collection_basis,
                previous_value=None,
                change=None,
                timestamp=now,
                source="analytics.share_of_voice",
            )
        )
    if sov.metrics:
        top = max(sov.metrics, key=lambda m: m.share_of_voice)
        insights.append(
            Insight(
                category="Visibility",
                text=f"{top.brand} has the highest search Share of Voice ({fmt_pct(top.share_of_voice)}).",
                metric="share_of_voice",
                entity=top.brand,
                current_value=fmt_pct(top.share_of_voice),
                previous_value=None,
                change=None,
                timestamp=now,
                source="analytics.share_of_voice",
            )
        )

    rows, score = _compliance_for_filters(session, filters)
    brand_scores = compute_brand_scores(rows, config=load_compliance_score_config())
    scored_brands = [(b, s) for b, s in brand_scores.items() if s.overall_score is not None]
    if scored_brands:
        best = max(scored_brands, key=lambda x: x[1].overall_score or 0)
        insights.append(
            Insight(
                category="Compliance",
                text=f"{best[0]} has the highest compliance score ({fmt_pct(best[1].overall_score)}).",
                metric="compliance_score",
                entity=best[0],
                current_value=fmt_pct(best[1].overall_score),
                previous_value=None,
                change=None,
                timestamp=now,
                source="analytics.compliance.compute_brand_scores",
            )
        )

    retailer_cmp = compare_by_retailer(session, scope=to_pricing_scope(filters))
    # Compare within same currency only
    by_ccy: dict[str, list] = {}
    for row in retailer_cmp:
        if row.average_price is None:
            continue
        by_ccy.setdefault(row.currency, []).append(row)
    for ccy, rows_r in by_ccy.items():
        if len(rows_r) < 2:
            continue
        ordered = sorted(rows_r, key=lambda r: r.average_price or Decimal(0))
        low, high = ordered[0], ordered[-1]
        insights.append(
            Insight(
                category="Retailer comparison",
                text=(
                    f"{low.value} has lower average price than {high.value} "
                    f"({fmt_money(low.average_price, ccy)} vs {fmt_money(high.average_price, ccy)})."
                ),
                metric="average_price",
                entity=f"{low.value}/{high.value}",
                current_value=fmt_money(low.average_price, ccy),
                previous_value=fmt_money(high.average_price, ccy),
                change=None,
                timestamp=now,
                source="analytics.pricing.compare_by_retailer",
            )
        )

    return insights


def top_movers(session: Session, filters: DashboardFilters, *, mode: str = "brands") -> list[dict[str, Any]]:
    prev = previous_period(filters)
    if prev is None:
        return []
    movers: list[dict[str, Any]] = []
    if mode == "brands":
        cur = share_of_shelf_by_brand(session, scope=to_sos_scope(filters))
        old = share_of_shelf_by_brand(session, scope=to_sos_scope(prev))
        old_map = {s.value: s for s in old.shares}
        for s in cur.shares:
            o = old_map.get(s.value)
            if o is None:
                continue
            delta = float(s.share) - float(o.share)
            movers.append(
                {
                    "entity": s.value,
                    "metric": "Share of Shelf",
                    "current": float(s.share),
                    "previous": float(o.share),
                    "change_pp": delta * 100.0,
                }
            )
        movers.sort(key=lambda m: abs(m["change_pp"]), reverse=True)
        return movers[:15]

    # Products: price movers
    scope = to_pricing_scope(filters)
    obs = list_price_observations(session, scope=scope, latest_only=False)
    by_pid: dict[int, list] = {}
    for o in obs:
        by_pid.setdefault(o.product_id, []).append(o)
    for pid, rows in by_pid.items():
        rows_sorted = sorted(rows, key=lambda r: r.observed_at, reverse=True)
        if len(rows_sorted) < 2:
            continue
        a, b = rows_sorted[0], rows_sorted[1]
        if a.current_price is None or b.current_price is None or b.current_price == 0:
            continue
        delta_pct = float((a.current_price - b.current_price) / b.current_price * 100)
        movers.append(
            {
                "entity": f"product:{pid}",
                "product_id": pid,
                "brand": a.brand,
                "metric": "Price",
                "current": float(a.current_price),
                "previous": float(b.current_price),
                "change_pct": delta_pct,
                "currency": a.currency,
            }
        )
    movers.sort(key=lambda m: abs(m.get("change_pct", 0)), reverse=True)
    return movers[:15]


def retailer_search_coverage(session: Session, retailer_code: str) -> CoverageDisplay:
    """Compact search-coverage summary from existing Share of Voice snapshot + stratum budgets."""
    from collector.universe_config import strata_for

    snap = share_of_voice(session, scope=to_sov_scope(DashboardFilters(retailer_code=retailer_code)))
    specs = strata_for(retailer_code)
    budget = sum(int(spec.budget) for spec in specs) if specs else None
    observed = snap.total_observations
    basis = snap.collection_basis or "empty"
    if observed <= 0 and basis in {"empty", "NO_DATA"}:
        observed_n: Optional[int] = None
    else:
        observed_n = observed
    headline, status, detail = format_search_coverage(
        observed=observed_n,
        budget=budget,
        basis=basis,
    )
    return CoverageDisplay(
        retailer_code=retailer_code,
        observed=observed_n,
        budget=budget,
        basis=basis,
        status=status,
        headline=headline,
        detail=detail,
    )


def available_currencies(session: Session, filters: DashboardFilters) -> list[str]:
    scope = to_pricing_scope(DashboardFilters(
        retailer_code=filters.retailer_code,
        country_code=filters.country_code,
        product_type=filters.product_type,
        brand=filters.brand,
        date_from=filters.date_from,
        date_to=filters.date_to,
        currency=None,
    ))
    obs = list_price_observations(session, scope=scope, latest_only=True)
    return sorted({o.currency for o in obs if o.currency})


def tracked_brand_names() -> tuple[str, ...]:
    from collector.search.config import load_sov_config

    return tuple(load_sov_config().tracked_brands)


# Re-export analytics wrappers used by pages
__all__ = [
    "Insight",
    "AlertItem",
    "retailer_search_coverage",
    "available_currencies",
    "tracked_brand_names",
    "metric_tracked_products",
    "metric_share_of_shelf",
    "metric_average_price",
    "metric_average_discount",
    "metric_compliance",
    "metric_banner_share",
    "metric_share_of_voice",
    "build_alerts",
    "build_insights",
    "top_movers",
    "_avg_price_overall",
    "_median_price_overall",
    "_count_price_changes",
    "_compliance_for_filters",
    "filter_option_values",
    "load_collection_status",
    "count_tracked_products",
    "list_sku_rows",
    "product_detail",
    "share_of_shelf_by_brand",
    "share_of_shelf_by_oem",
    "share_of_shelf_trends",
    "average_price_by_brand",
    "median_price_by_brand",
    "average_discount",
    "count_discounted_products",
    "list_price_observations",
    "price_change_over_time",
    "discount_change_over_time",
    "compare_by_retailer",
    "compare_by_country",
    "compare_by_product_type",
    "banner_share_by_brand",
    "share_of_voice",
    "share_of_voice_trends",
    "keyword_metrics",
    "highest_visibility_by_retailer",
    "highest_cross_retailer_visibility",
    "list_cross_retailer_visibility",
    "list_product_visibility",
    "compute_compliance_score",
    "compute_brand_scores",
    "compute_retailer_scores",
    "compute_country_scores",
    "load_audit_rows",
    "load_compliance_score_config",
    "crosswalk_summary",
    "retailer_product_counts",
    "MATCHED_STATUS",
    "to_pricing_scope",
    "to_sos_scope",
    "to_sov_scope",
    "to_banner_scope",
    "to_visibility_scope",
    "previous_period",
]
