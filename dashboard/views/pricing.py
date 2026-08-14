"""Pricing & Promotions section."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import horizontal_share_bars
from dashboard.components.filters_ui import select_currency
from dashboard.components.kpi_cards import render_kpi_card
from dashboard.components.layout import card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters, to_pricing_scope
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import (
    _avg_price_overall,
    _median_price_overall,
    available_currencies,
    average_discount,
    average_price_by_brand,
    count_discounted_products,
)
from dashboard.utils.format import fmt_money, fmt_pct
from dashboard.utils.semantics import DataState, MetricValue


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection
    with card():
        section_header(
            "Pricing & Promotions",
            "Average listed price and promotion activity in a single currency. USD and BRL are never mixed.",
        )
        currencies = available_currencies(session, filters)
        currency = select_currency("price_currency", currencies) if currencies else None
        scoped = replace(filters, currency=currency)
        scope = to_pricing_scope(scoped)

        avg, ccy, n = _avg_price_overall(session, scope)
        med, mccy, mn = _median_price_overall(session, scope)
        disc = average_discount(session, scope=scope)
        discounted = count_discounted_products(session, scope=scope)
        brand_rows = average_price_by_brand(session, scope=scope)

        def price_metric(val, cur, count, definition: str) -> MetricValue:
            if cur == "MIXED":
                return MetricValue(
                    state=DataState.INSUFFICIENT,
                    display="Select a currency",
                    detail="USD and BRL stay separate",
                    denominator=count,
                    source="analytics.pricing",
                    definition=definition,
                )
            if val is None:
                return MetricValue(
                    state=DataState.NO_DATA,
                    display="N/A",
                    detail="No data",
                    denominator=count,
                    source="analytics.pricing",
                    definition=definition,
                )
            return MetricValue.from_number(
                float(val),
                display=fmt_money(val, cur),
                denominator=count,
                source="analytics.pricing",
                definition=definition,
            )

        left, right = st.columns([1.35, 1], gap="large")
        with left:
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                render_kpi_card("Average Price", price_metric(avg, ccy, n, "Mean of latest current_price"), timestamp=refreshed_at)
            with k2:
                render_kpi_card("Median Price", price_metric(med, mccy, mn, "Median of latest current_price"), timestamp=refreshed_at)
            with k3:
                render_kpi_card(
                    "Discounted Products",
                    MetricValue.from_number(discounted, display=str(discounted), source="analytics.pricing"),
                    timestamp=refreshed_at,
                )
            with k4:
                render_kpi_card(
                    "Average Discount",
                    MetricValue.from_number(
                        float(disc) if disc is not None else None,
                        display=fmt_pct(disc, already_ratio=False) if disc is not None else "N/A",
                        source="analytics.pricing.average_discount",
                        definition="Mean discount_pct among observations that have discount_pct",
                    ),
                    timestamp=refreshed_at,
                )

            pdf = pd.DataFrame(
                [
                    {
                        "brand": r.value,
                        "average": float(r.average_price) if r.average_price is not None else None,
                        "products": r.product_count,
                        "currency": r.currency,
                    }
                    for r in brand_rows
                    if r.average_price is not None and (currency is None or r.currency == currency)
                ]
            )
            axis = f"Average price ({currency})" if currency else "Average price"
            horizontal_share_bars(
                pdf,
                category_col="brand",
                value_col="average",
                title="Average Price by Brand",
                definition="Mean of latest current_price observations by brand, in one currency.",
                source="analytics.pricing.average_price_by_brand",
                filters_label=scoped.label_summary(),
                x_title=axis,
                hover_extra=["products", "currency"],
                value_is_pct=False,
                height=260,
                empty_title="Pricing unavailable",
                empty_explanation="No priced observations are available for the selected currency.",
            )

        with right:
            st.markdown("**Promotion Activity**")
            promo = pd.DataFrame(
                [
                    {
                        "Brand": r.value,
                        "Products": r.product_count,
                        "On Promo": r.discounted_product_count,
                        "Avg Discount": (
                            f"{float(r.average_discount_pct):.1f}%"
                            if r.average_discount_pct is not None
                            else "N/A"
                        ),
                    }
                    for r in brand_rows
                    if currency is None or r.currency == currency
                ]
            )
            show_dataframe(
                promo,
                empty_message="No promotion activity",
                empty_explanation="No priced brand rows for the selected currency.",
                height=280,
            )
            st.caption("Source: analytics.pricing.average_price_by_brand · currencies are never mixed.")
