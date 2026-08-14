"""Pricing & Promotions section."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import horizontal_share_bars
from dashboard.components.filters_ui import select_currency
from dashboard.components.kpi_cards import kpi_card_html
from dashboard.components.layout import card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters, to_pricing_scope
from dashboard.insights_text import NO_DATA
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
    del collection, refreshed_at
    with card():
        section_header(
            "Pricing & Promotions",
            "Compare pricing and promotional activity across brands.",
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

        def price_metric(val, cur, count) -> MetricValue:
            if cur == "MIXED":
                return MetricValue(
                    state=DataState.INSUFFICIENT,
                    display="Select a currency",
                    detail="USD and BRL stay separate",
                    denominator=count,
                )
            if val is None:
                return MetricValue(
                    state=DataState.NO_DATA,
                    display="N/A",
                    detail="No data",
                    denominator=count,
                )
            return MetricValue.from_number(
                float(val),
                display=fmt_money(val, cur),
                denominator=count,
            )

        cards = [
            ("Average Price", price_metric(avg, ccy, n)),
            ("Median Price", price_metric(med, mccy, mn)),
            ("Discounted Products", MetricValue.from_number(discounted, display=str(discounted))),
            (
                "Average Discount",
                MetricValue.from_number(
                    float(disc) if disc is not None else None,
                    display=fmt_pct(disc, already_ratio=False) if disc is not None else "N/A",
                ),
            ),
        ]
        inner = "".join(kpi_card_html(label, metric) for label, metric in cards)
        st.markdown(f'<div class="ci-price-kpis">{inner}</div>', unsafe_allow_html=True)

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
            definition="",
            source="analytics.pricing.average_price_by_brand",
            filters_label=scoped.label_summary(),
            x_title=axis,
            hover_extra=["products", "currency"],
            value_is_pct=False,
            height=280,
            empty_title="Pricing unavailable",
            empty_explanation=NO_DATA,
        )

        st.markdown("**Promotion Snapshot**")
        promo = pd.DataFrame(
            [
                {
                    "Brand": r.value,
                    "Products": r.product_count,
                    "On Promotion": r.discounted_product_count,
                    "Avg. Discount": (
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
            empty_explanation=NO_DATA,
            height=260,
        )
