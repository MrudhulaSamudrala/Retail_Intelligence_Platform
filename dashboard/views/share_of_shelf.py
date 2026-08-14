"""Share of Shelf section — one horizontal bar chart."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import horizontal_share_bars
from dashboard.components.filters_ui import select_retailer, select_stratum
from dashboard.components.layout import card, section_header
from dashboard.filters import DashboardFilters, previous_period, to_sos_scope
from dashboard.presentation import brand_sort_key
from dashboard.queries.collection import CollectionStatusSnapshot, filter_option_values
from dashboard.services import share_of_shelf_by_brand
from dashboard.utils.format import fmt_change


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
    with card():
        section_header(
            "Share of Shelf",
            "Percentage of eligible gaming products represented by each platform.",
        )
        options = filter_option_values(session)
        c1, c2 = st.columns(2)
        with c1:
            retailer = select_retailer("sos_retailer", options.get("retailer_code", []))
        with c2:
            stratum = select_stratum("sos_stratum")

        scoped = replace(filters, retailer_code=retailer, product_type=stratum, brand=None)
        snap = share_of_shelf_by_brand(session, scope=to_sos_scope(scoped))
        rows = [
            {
                "brand": s.value,
                "share_pct": float(s.share) * 100.0,
                "product_count": s.product_count,
                "universe_size": s.universe_size,
            }
            for s in snap.shares
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df["_ord"] = df["brand"].map(brand_sort_key)
            df = df.sort_values("_ord").drop(columns=["_ord"])

        unavailable = snap.collection_status == "NO_DATA" and snap.universe_size == 0
        horizontal_share_bars(
            pd.DataFrame() if unavailable else df,
            category_col="brand",
            value_col="share_pct",
            title="Share of Shelf",
            definition="Brand product count / eligible tracked universe (sos_universe_v1).",
            source="analytics.share_of_shelf_by_brand",
            filters_label=scoped.label_summary(),
            x_title="Share of Shelf (%)",
            hover_extra=["product_count", "universe_size"],
            value_is_pct=True,
            height=260,
            empty_title="Share of Shelf unavailable",
            empty_explanation="The current collection does not contain an eligible product universe for these filters.",
        )
        prior = previous_period(scoped)
        if prior is not None and not unavailable and snap.universe_size > 0:
            prev_snap = share_of_shelf_by_brand(session, scope=to_sos_scope(prior))
            if prev_snap.universe_size > 0 and snap.shares and prev_snap.shares:
                lead = max(snap.shares, key=lambda s: float(s.share))
                prev_share = next(
                    (float(s.share) for s in prev_snap.shares if s.value == lead.value),
                    None,
                )
                _, trend = fmt_change(
                    float(lead.share),
                    prev_share,
                    as_pct_points=True,
                    already_ratio=True,
                )
                if trend != "Insufficient data":
                    st.caption(f"{lead.value}: {trend}")
