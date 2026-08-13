"""Deterministic Insights page."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.header import render_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import build_insights


CATEGORIES = [
    "Pricing",
    "Share of Shelf",
    "Compliance",
    "Visibility",
    "Promotions",
    "Retailer comparison",
    "Country comparison",
]


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="Insights",
        subtitle="Deterministic, data-traceable insights from PostgreSQL analytics (no LLM narrative).",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )

    insights = build_insights(session, filters)
    if not insights:
        st.info("No supported insights for the selected filters / available history.")
        return

    selected = st.multiselect("Categories", CATEGORIES, default=CATEGORIES)
    filtered = [i for i in insights if i.category in selected]
    if not filtered:
        st.info("No insights in the selected categories.")
        return

    for ins in filtered:
        with st.expander(f"[{ins.category}] {ins.text}"):
            st.write(
                {
                    "metric": ins.metric,
                    "entity": ins.entity,
                    "current_value": ins.current_value,
                    "previous_value": ins.previous_value,
                    "change": ins.change,
                    "timestamp": str(ins.timestamp),
                    "source": ins.source,
                    "detail_key": ins.detail_key,
                }
            )
            st.caption("Underlying record/query reference is the `source` analytics function above.")

    show_dataframe(
        pd.DataFrame(
            [
                {
                    "category": i.category,
                    "insight": i.text,
                    "metric": i.metric,
                    "entity": i.entity,
                    "current": i.current_value,
                    "previous": i.previous_value,
                    "change": i.change,
                    "source": i.source,
                    "timestamp": i.timestamp,
                }
                for i in filtered
            ]
        )
    )
