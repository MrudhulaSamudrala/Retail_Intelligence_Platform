"""Product Data Attributes section."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from dashboard.components.charts import horizontal_share_bars
from dashboard.components.layout import card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.queries.catalog import attribute_coverage, list_sku_rows
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_money


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
    with card():
        section_header(
            "Product Data Attributes",
            "Coverage of stored catalog attributes on currently tracked products.",
        )
        coverage = attribute_coverage(session, filters=filters)
        cdf = pd.DataFrame(coverage)
        horizontal_share_bars(
            cdf,
            category_col="attribute",
            value_col="coverage_pct",
            title="Attribute Coverage",
            definition="Products with a non-empty stored value / tracked products in view.",
            source="dashboard.queries.catalog.attribute_coverage",
            filters_label=filters.label_summary(),
            x_title="Coverage (%)",
            hover_extra=["present", "total"],
            value_is_pct=True,
            height=260,
            empty_title="No product attributes",
            empty_explanation="No active products are available to measure attribute coverage.",
        )
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
        show_dataframe(
            table,
            empty_message="No recent products",
            empty_explanation="No catalog rows match the current filters.",
            height=280,
        )
