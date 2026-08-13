"""Share of Shelf page."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from dashboard.components.charts import bar_by_brand, line_sos_trend, stacked_bar_sos_by_retailer
from dashboard.components.header import render_header
from dashboard.components.kpi_cards import render_kpi_card
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters, to_sos_scope
from dashboard.views.overview import _sos_by_retailer_df, _sos_trend_df
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import share_of_shelf_by_brand, share_of_shelf_by_oem
from dashboard.utils.format import fmt_pct
from dashboard.utils.semantics import MetricValue
from database.models import Product


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="Share of Shelf",
        subtitle="Eligible universe share using sos_universe_v1 (accessories excluded; Apple not double-counted).",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )

    snap = share_of_shelf_by_brand(session, scope=to_sos_scope(filters))
    oem_snap = share_of_shelf_by_oem(session, scope=to_sos_scope(filters))
    excl = snap.exclusions

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card(
            "Eligible universe",
            MetricValue.from_number(
                snap.universe_size,
                display=str(snap.universe_size),
                source="analytics.share_of_shelf",
                definition="sos_universe_v1 eligible listings",
                detail=f"rules={snap.inclusion_rules_id}",
            ),
            timestamp=refreshed_at,
        )
    with k2:
        excluded_total = (
            excl.accessory_or_ineligible_type
            + excl.non_gaming
            + excl.missing_identity
            + excl.scope_filtered
            + excl.inactive
        )
        render_kpi_card(
            "Excluded products",
            MetricValue.from_number(excluded_total, display=str(excluded_total), source="sos exclusions"),
            timestamp=refreshed_at,
        )
    with k3:
        top = snap.shares[0] if snap.shares else None
        render_kpi_card(
            "Top brand share",
            MetricValue.from_number(
                float(top.share) if top else None,
                display=f"{top.value}: {fmt_pct(top.share)}" if top else "No data available",
                denominator=snap.universe_size,
                numerator=top.product_count if top else None,
                source="analytics.share_of_shelf_by_brand",
            ),
            timestamp=refreshed_at,
        )
    with k4:
        render_kpi_card(
            "Brands in universe",
            MetricValue.from_number(len(snap.shares), display=str(len(snap.shares)), source="sos"),
            timestamp=refreshed_at,
        )

    if collection.is_partial:
        st.warning("PARTIAL DATA — Share of Shelf may under-count the true shelf.")

    st.subheader("Exclusion reasons")
    show_dataframe(
        pd.DataFrame(
            [
                {"reason": "accessory_or_ineligible_type", "count": excl.accessory_or_ineligible_type},
                {"reason": "non_gaming", "count": excl.non_gaming},
                {"reason": "missing_identity", "count": excl.missing_identity},
                {"reason": "scope_filtered", "count": excl.scope_filtered},
                {"reason": "inactive", "count": excl.inactive},
            ]
        )
    )

    brand_df = pd.DataFrame(
        [
            {
                "brand": s.value,
                "product_count": s.product_count,
                "share_pct": float(s.share) * 100.0,
                "universe_size": s.universe_size,
            }
            for s in snap.shares
        ]
    )
    bar_by_brand(
        brand_df,
        value_col="share_pct",
        title="Share by brand",
        definition="Brand count / eligible universe",
        source="analytics.share_of_shelf_by_brand",
        filters_label=filters.label_summary(),
        hover_extra=["product_count", "universe_size"],
    )

    # Fix: bar_by_brand expects brand column — brand_df already has it
    st.subheader("Product count comparison")
    show_dataframe(brand_df)

    st.subheader("Share by retailer")
    mode = st.radio("Mode", ["percentage", "absolute product count"], horizontal=True, key="sos_page_mode")
    stacked_bar_sos_by_retailer(
        _sos_by_retailer_df(session, filters),
        mode="percentage" if mode.startswith("percentage") else "count",
        filters_label=filters.label_summary(),
    )

    st.subheader("Country / product-type breakdown")
    countries = session.scalars(select(distinct(Product.country_code))).all()
    types = session.scalars(select(distinct(Product.product_type)).where(Product.product_type.is_not(None))).all()
    c_rows = []
    for country in countries:
        if filters.country_code and country != filters.country_code:
            continue
        scoped = DashboardFilters(
            retailer_code=filters.retailer_code,
            country_code=country,
            product_type=filters.product_type,
            brand=filters.brand,
            oem=filters.oem,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )
        s = share_of_shelf_by_brand(session, scope=to_sos_scope(scoped))
        for sh in s.shares:
            c_rows.append(
                {
                    "country": country,
                    "brand": sh.value,
                    "share_pct": float(sh.share) * 100,
                    "count": sh.product_count,
                    "universe": s.universe_size,
                }
            )
    show_dataframe(pd.DataFrame(c_rows), empty_message="No country SoS data.")

    t_rows = []
    for pt in types:
        if filters.product_type and pt != filters.product_type:
            continue
        scoped = DashboardFilters(
            retailer_code=filters.retailer_code,
            country_code=filters.country_code,
            product_type=pt,
            brand=filters.brand,
            oem=filters.oem,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )
        s = share_of_shelf_by_brand(session, scope=to_sos_scope(scoped))
        for sh in s.shares:
            t_rows.append(
                {
                    "product_type": pt,
                    "brand": sh.value,
                    "share_pct": float(sh.share) * 100,
                    "count": sh.product_count,
                    "universe": s.universe_size,
                }
            )
    show_dataframe(pd.DataFrame(t_rows), empty_message="No product-type SoS data.")

    st.subheader("OEM drilldown")
    oem_df = pd.DataFrame(
        [
            {
                "oem": s.value,
                "product_count": s.product_count,
                "share_pct": float(s.share) * 100.0,
                "universe_size": s.universe_size,
            }
            for s in oem_snap.shares
        ]
    )
    show_dataframe(oem_df, empty_message="No OEM share data.")

    st.subheader("Historical trend")
    line_sos_trend(
        _sos_trend_df(session, filters),
        filters_label=filters.label_summary(),
        partial=collection.is_partial,
    )
