"""Executive Overview page."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard.components.alerts import render_alerts
from dashboard.components.charts import (
    bar_by_brand,
    compliance_donut,
    line_sos_trend,
    stacked_bar_sos_by_retailer,
)
from dashboard.components.header import render_header
from dashboard.components.kpi_cards import comparison_delta, render_kpi_card
from dashboard.components.tables import show_dataframe
from dashboard.services import (
    _compliance_for_filters,
    _count_price_changes,
    average_price_by_brand,
    build_alerts,
    build_insights,
    metric_average_discount,
    metric_average_price,
    metric_banner_share,
    metric_compliance,
    metric_share_of_shelf,
    metric_share_of_voice,
    metric_tracked_products,
    share_of_shelf_by_brand,
    share_of_shelf_trends,
    top_movers,
)
from dashboard.filters import DashboardFilters, previous_period, to_pricing_scope, to_sos_scope
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_pct
from dashboard.utils.semantics import MetricValue
from sqlalchemy.orm import Session


def _sos_trend_df(session: Session, filters: DashboardFilters) -> pd.DataFrame:
    points = share_of_shelf_trends(session, scope=to_sos_scope(filters))
    rows = []
    for pt in points:
        for s in pt.shares:
            rows.append(
                {
                    "date": pt.period_start,
                    "brand": s.value,
                    "share_pct": float(s.share) * 100.0,
                    "eligible_count": s.product_count,
                    "universe_size": s.universe_size,
                }
            )
    return pd.DataFrame(rows)


def _sos_by_retailer_df(session: Session, filters: DashboardFilters) -> pd.DataFrame:
    from database.models import Product
    from sqlalchemy import distinct, select

    retailers = session.scalars(select(distinct(Product.retailer_code))).all()
    rows = []
    for retailer in retailers:
        if filters.retailer_code and retailer != filters.retailer_code:
            continue
        scoped = DashboardFilters(
            retailer_code=retailer,
            country_code=filters.country_code,
            product_type=filters.product_type,
            brand=filters.brand,
            oem=filters.oem,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )
        snap = share_of_shelf_by_brand(session, scope=to_sos_scope(scoped))
        for s in snap.shares:
            rows.append(
                {
                    "retailer": retailer,
                    "brand": s.value,
                    "product_count": s.product_count,
                    "share_pct": float(s.share) * 100.0,
                    "universe_size": snap.universe_size,
                }
            )
    return pd.DataFrame(rows)


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="Executive Overview",
        subtitle="Key competitive intelligence metrics for the selected period.",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )

    prev = previous_period(filters)
    now = refreshed_at

    m_products = metric_tracked_products(session, filters)
    m_sos, sos_snap = metric_share_of_shelf(session, filters)
    m_price = metric_average_price(session, filters)
    m_disc = metric_average_discount(session, filters)
    m_comp, comp_score = metric_compliance(session, filters)
    m_banner, _banner = metric_banner_share(session, filters)
    m_sov, sov_snap = metric_share_of_voice(session, filters)
    price_changes = _count_price_changes(session, filters)
    fail_unknown_rows, _ = _compliance_for_filters(session, filters)
    compliance_issues = sum(1 for r in fail_unknown_rows if r.result == "FAIL")

    # Previous period comparisons where possible
    def prev_metric(builder):
        if prev is None:
            return None
        return builder(session, prev)

    p_price = prev_metric(metric_average_price) if prev else None
    p_disc = prev_metric(metric_average_discount) if prev else None
    p_sos = None
    if prev is not None:
        p_sos, _ = metric_share_of_shelf(session, prev)

    st.markdown('<div class="ci-section-title">KPI snapshot</div>', unsafe_allow_html=True)
    r1 = st.columns(5)
    with r1[0]:
        render_kpi_card("Total Tracked Products", m_products, timestamp=now)
    with r1[1]:
        d_dir, d_lbl = (None, None)
        if p_sos and m_sos.value is not None and p_sos.value is not None:
            d_dir, d_lbl = comparison_delta(m_sos.value, p_sos.value, as_pp=True, already_ratio=True)
        render_kpi_card("Share of Shelf (top brand)", m_sos, delta_label=d_lbl, delta_direction=d_dir, timestamp=now)
    with r1[2]:
        d_dir, d_lbl = (None, None)
        if p_price and m_price.value is not None and p_price.value is not None:
            d_dir, d_lbl = comparison_delta(m_price.value, p_price.value)
        render_kpi_card("Average Price", m_price, delta_label=d_lbl, delta_direction=d_dir, timestamp=now)
    with r1[3]:
        d_dir, d_lbl = (None, None)
        if p_disc and m_disc.value is not None and p_disc.value is not None:
            d_dir, d_lbl = comparison_delta(m_disc.value, p_disc.value)
        render_kpi_card("Average Discount", m_disc, delta_label=d_lbl, delta_direction=d_dir, timestamp=now)
    with r1[4]:
        render_kpi_card("Compliance Score", m_comp, timestamp=now)

    r2 = st.columns(5)
    with r2[0]:
        render_kpi_card("Banner Share (top brand)", m_banner, timestamp=now)
    with r2[1]:
        render_kpi_card("Search Visibility / SoV", m_sov, timestamp=now)
    with r2[2]:
        render_kpi_card(
            "Products with price changes",
            MetricValue.from_number(
                price_changes,
                display=str(price_changes),
                source="analytics.pricing",
                definition="Products whose latest two price observations differ",
            ),
            timestamp=now,
        )
    with r2[3]:
        render_kpi_card(
            "Products with compliance issues",
            MetricValue.from_number(
                compliance_issues,
                display=str(compliance_issues),
                source="analytics.compliance",
                definition="Count of FAIL audit rows in scope (UNKNOWN excluded)",
            ),
            timestamp=now,
        )
    with r2[4]:
        health = collection.freshness_label
        render_kpi_card(
            "Data Collection Health",
            MetricValue.from_number(
                1 if collection.is_live else (0 if collection.is_partial or collection.is_stale else None),
                display=health,
                source="collection_runs / collection_run_steps",
                definition="Derived from latest collection run + component steps",
                detail=collection.latest_status,
            ),
            timestamp=collection.last_successful_at,
        )

    st.markdown("---")
    st.markdown('<div class="ci-section-title">A. Share of Shelf Trend</div>', unsafe_allow_html=True)
    trend_df = _sos_trend_df(session, filters)
    line_sos_trend(
        trend_df,
        filters_label=filters.label_summary(),
        partial=collection.is_partial,
    )

    st.markdown('<div class="ci-section-title">B. Share of Shelf by Retailer</div>', unsafe_allow_html=True)
    mode = st.radio("Display", ["percentage", "absolute product count"], horizontal=True, key="sos_retailer_mode")
    sos_ret = _sos_by_retailer_df(session, filters)
    stacked_bar_sos_by_retailer(
        sos_ret,
        mode="percentage" if mode.startswith("percentage") else "count",
        filters_label=filters.label_summary(),
    )

    c_left, c_right = st.columns(2)
    brand_prices = average_price_by_brand(session, scope=to_pricing_scope(filters))
    with c_left:
        st.markdown('<div class="ci-section-title">C. Average Price by Brand</div>', unsafe_allow_html=True)
        price_mode = st.radio("Price statistic", ["average", "median"], horizontal=True, key="price_stat_mode")
        pdf = pd.DataFrame(
            [
                {
                    "brand": r.value,
                    "currency": r.currency,
                    "average": float(r.average_price) if r.average_price is not None else None,
                    "median": float(r.median_price) if r.median_price is not None else None,
                    "products": r.product_count,
                }
                for r in brand_prices
            ]
        )
        if not pdf.empty:
            pdf = pdf.dropna(subset=[price_mode])
        bar_by_brand(
            pdf,
            value_col=price_mode,
            title=f"{price_mode.title()} Price by Brand",
            definition="Latest priced observations; currencies shown separately (never silently mixed)",
            source="analytics.pricing.average_price_by_brand",
            filters_label=filters.label_summary(),
            hover_extra=["products", "currency"],
        )
    with c_right:
        st.markdown('<div class="ci-section-title">D. Average Discount by Brand</div>', unsafe_allow_html=True)
        ddf = pd.DataFrame(
            [
                {
                    "brand": r.value,
                    "currency": r.currency,
                    "avg_discount_pct": float(r.average_discount_pct)
                    if r.average_discount_pct is not None
                    else None,
                    "discounted_products": r.discounted_product_count,
                }
                for r in brand_prices
                if r.average_discount_pct is not None
            ]
        )
        bar_by_brand(
            ddf,
            value_col="avg_discount_pct",
            title="Average Discount % by Brand",
            definition="Mean discount_pct only for products with valid discount observations",
            source="analytics.pricing.average_price_by_brand",
            filters_label=filters.label_summary(),
            hover_extra=["discounted_products", "currency"],
        )

    st.markdown('<div class="ci-section-title">E. Compliance Overview</div>', unsafe_allow_html=True)
    st.caption("Notebook = 85% · Desktop = 15% · UNKNOWN is not treated as FAIL")
    if comp_score.overall_score is None:
        st.warning("N/A / insufficient data for overall weighted compliance score.")
    else:
        st.metric("Overall compliance score", fmt_pct(comp_score.overall_score))
    nb = comp_score.notebook
    dt = comp_score.desktop
    c1, c2, c3 = st.columns(3)
    with c1:
        st.write(
            "Notebook:",
            fmt_pct(nb.score) if nb and nb.score is not None else "N/A / insufficient data",
        )
    with c2:
        st.write(
            "Desktop:",
            fmt_pct(dt.score) if dt and dt.score is not None else "N/A / insufficient data",
        )
    with c3:
        st.write(
            f"PASS={comp_score.coverage.pass_count} · "
            f"FAIL={comp_score.coverage.fail_count} · "
            f"UNKNOWN={comp_score.coverage.unknown_count}"
        )

    check_map: dict[str, float | None] = {}
    for seg in (nb, dt):
        if seg is None:
            continue
        for code, cs in seg.check_scores.items():
            # Prefer notebook then fill gaps — display only scored rates
            if code not in check_map and cs.score is not None:
                check_map[code] = cs.score
    for code in ["S1", "S2", "P1", "P2", "P3", "P4", "P5"]:
        check_map.setdefault(code, None)
        # Show UNKNOWN-heavy checks as None if never scored
    compliance_donut(check_map, filters_label=filters.label_summary())

    left, right = st.columns(2)
    with left:
        st.markdown('<div class="ci-section-title">F. Top Insights</div>', unsafe_allow_html=True)
        insights = build_insights(session, filters)[:8]
        if not insights:
            st.info("No deterministic insights available for the selected filters.")
        for ins in insights:
            with st.expander(ins.text):
                st.write(
                    {
                        "category": ins.category,
                        "metric": ins.metric,
                        "entity": ins.entity,
                        "current": ins.current_value,
                        "previous": ins.previous_value,
                        "change": ins.change,
                        "timestamp": str(ins.timestamp),
                        "source": ins.source,
                    }
                )
                st.caption("View details → expand shows underlying metric reference.")
    with right:
        st.markdown('<div class="ci-section-title">G. Top Movers</div>', unsafe_allow_html=True)
        mode = st.radio("Movers", ["Brands", "Products"], horizontal=True, key="movers_mode")
        movers = top_movers(session, filters, mode="brands" if mode == "Brands" else "products")
        show_dataframe(pd.DataFrame(movers), empty_message="Insufficient historical data for movers.")

    render_alerts(
        build_alerts(
            session,
            filters,
            collection=collection,
            compliance_score=comp_score,
            sos_snap=sos_snap,
            sov_snap=sov_snap,
        )
    )
