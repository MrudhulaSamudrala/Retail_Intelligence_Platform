"""Product Data Quality section."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import horizontal_share_bars
from dashboard.components.layout import card, insight_card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.insights_text import NO_DATA, attribute_quality_insight
from dashboard.queries.catalog import attribute_coverage, list_sku_rows
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_money

_ATTR_ORDER = ("Brand", "Price", "RAM", "Graphics", "Processor", "Storage")


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
    with card():
        section_header(
            "Product Data Quality",
            "How complete is the product information across the tracked catalog?",
        )
        coverage = attribute_coverage(session, filters=filters)
        order = {name: idx for idx, name in enumerate(_ATTR_ORDER)}
        cdf = pd.DataFrame(coverage)
        if not cdf.empty:
            cdf["_ord"] = cdf["attribute"].map(lambda n: order.get(n, 99))
            cdf = cdf.sort_values("_ord").drop(columns=["_ord"])
        horizontal_share_bars(
            cdf,
            category_col="attribute",
            value_col="coverage_pct",
            title="Attribute Coverage",
            definition="",
            source="dashboard.queries.catalog.attribute_coverage",
            filters_label=filters.label_summary(),
            x_title="Coverage (%)",
            hover_extra=["present", "total"],
            value_is_pct=True,
            height=280,
            empty_title="No product attributes",
            empty_explanation=NO_DATA,
        )
        insight_card(attribute_quality_insight(coverage))
        recent = list_sku_rows(session, filters=filters, limit=12)
        table = pd.DataFrame(
            [
                {
                    "Product": (r.title or r.retailer_sku or f"product:{r.product_id}")[:56],
                    "Brand": r.brand or "—",
                    "Processor": r.processor or "—",
                    "GPU": r.gpu or "—",
                    "RAM": r.ram or "—",
                    "Storage": r.storage or "—",
                    "Price": fmt_money(r.current_price, r.currency) if r.current_price is not None else "N/A",
                }
                for r in recent
            ]
        )
        with st.expander("View attribute details"):
            show_dataframe(
                table,
                empty_message="No recent products",
                empty_explanation=NO_DATA,
                height=280,
            )
