"""Search visibility (Share of Voice) section."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import horizontal_share_bars
from dashboard.components.filters_ui import select_keyword, select_retailer, select_stratum
from dashboard.components.layout import card, section_header, status_pill
from dashboard.filters import DashboardFilters, to_sov_scope
from dashboard.presentation import TRACKED_PLATFORM_BRANDS, brand_sort_key, ranked_visibility_available
from dashboard.queries.collection import filter_option_values, list_search_keywords
from dashboard.services import retailer_search_coverage, share_of_voice


def render_search(session: Session, filters: DashboardFilters) -> None:
    with card():
        options = filter_option_values(session)
        keywords = list_search_keywords(session)
        title_col, pill_col = st.columns([4, 1.4])
        with title_col:
            section_header(
                "Search Visibility",
                "Share of Voice across observed search results.",
            )
        c1, c2, c3 = st.columns(3)
        with c1:
            retailer = select_retailer("sov_retailer", options.get("retailer_code", []))
        with c2:
            stratum = select_stratum("sov_stratum")
        with c3:
            keyword = select_keyword("sov_keyword", keywords)

        scoped = replace(filters, retailer_code=retailer, stratum=stratum)
        snap = share_of_voice(session, scope=to_sov_scope(scoped, keyword=keyword))
        partial = snap.collection_basis in {"observed_partial", "mixed"} or snap.partial_searches > 0
        show_partial = False
        if retailer:
            cov = retailer_search_coverage(session, retailer)
            show_partial = cov.status == "PARTIAL"
        else:
            show_partial = partial
        with pill_col:
            if show_partial:
                st.markdown(
                    status_pill("PARTIAL RANKED COVERAGE", kind="warn"),
                    unsafe_allow_html=True,
                )

        metrics = [m for m in snap.metrics if m.brand in TRACKED_PLATFORM_BRANDS]
        has_metrics = ranked_visibility_available(snap.collection_basis, has_metrics=bool(metrics))
        df = pd.DataFrame(
            [
                {
                    "brand": m.brand,
                    "share_pct": float(m.share_of_voice) * 100.0,
                    "appearances": m.appearances,
                }
                for m in metrics
            ]
        )
        if not df.empty:
            df["_ord"] = df["brand"].map(brand_sort_key)
            df = df.sort_values("_ord").drop(columns=["_ord"])

        horizontal_share_bars(
            df if has_metrics else pd.DataFrame(),
            category_col="brand",
            value_col="share_pct",
            title="Share of Voice",
            definition="Brand search-result appearances / total tracked-brand appearances.",
            source="analytics.share_of_voice",
            filters_label=scoped.label_summary() + (f", Keyword={keyword}" if keyword else ""),
            x_title="Share of Voice (%)",
            hover_extra=["appearances"],
            value_is_pct=True,
            height=260,
            empty_title="Ranked visibility unavailable",
            empty_explanation="This retailer's current collection does not provide complete ranked search coverage.",
        )


def render(session: Session, filters: DashboardFilters) -> None:
    render_search(session, filters)
