"""Pricing & Promotions page."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import bar_by_brand, time_series
from dashboard.components.header import render_header
from dashboard.components.kpi_cards import render_kpi_card
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters, to_pricing_scope
from dashboard.queries.catalog import product_detail
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import (
    _avg_price_overall,
    _count_price_changes,
    _median_price_overall,
    average_discount,
    average_price_by_brand,
    compare_by_country,
    compare_by_product_type,
    compare_by_retailer,
    count_discounted_products,
    discount_change_over_time,
    list_price_observations,
    price_change_over_time,
)
from dashboard.utils.format import fmt_money, fmt_pct
from dashboard.utils.semantics import DataState, MetricValue


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="Pricing & Promotions",
        subtitle="Price and promotion analytics from PostgreSQL price history.",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )
    scope = to_pricing_scope(filters)

    avg, ccy, n = _avg_price_overall(session, scope)
    med, mccy, mn = _median_price_overall(session, scope)
    disc = average_discount(session, scope=scope)
    discounted = count_discounted_products(session, scope=scope)
    changed = _count_price_changes(session, filters)
    obs = list_price_observations(session, scope=scope, latest_only=True)
    promo_count = len({o.product_id for o in obs if o.is_on_promotion})

    def price_metric(val, currency, count, label_def):
        if currency == "MIXED":
            return MetricValue(
                state=DataState.INSUFFICIENT,
                display="Multiple currencies",
                detail="Filter to one retailer/country",
                denominator=count,
                source="analytics.pricing",
                definition=label_def,
            )
        return MetricValue.from_number(
            float(val) if val is not None else None,
            display=fmt_money(val, currency),
            denominator=count,
            source="analytics.pricing",
            definition=label_def,
        )

    cols = st.columns(6)
    cards = [
        ("Average Price", price_metric(avg, ccy, n, "Mean latest current_price")),
        ("Median Price", price_metric(med, mccy, mn, "Median latest current_price")),
        (
            "Average Discount",
            MetricValue.from_number(
                float(disc) if disc is not None else None,
                display=fmt_pct(disc, already_ratio=False) if disc is not None else "No data",
                source="analytics.pricing.average_discount",
                definition="Mean discount_pct among observations with discount_pct",
            ),
        ),
        (
            "Discounted Products",
            MetricValue.from_number(discounted, display=str(discounted), source="analytics.pricing"),
        ),
        (
            "Products with Price Changes",
            MetricValue.from_number(changed, display=str(changed), source="analytics.pricing"),
        ),
        (
            "Products with Promotions",
            MetricValue.from_number(promo_count, display=str(promo_count), source="analytics.pricing"),
        ),
    ]
    for col, (label, metric) in zip(cols, cards):
        with col:
            render_kpi_card(label, metric, timestamp=refreshed_at)

    brand_rows = average_price_by_brand(session, scope=scope)
    pdf = pd.DataFrame(
        [
            {
                "brand": r.value,
                "currency": r.currency,
                "average": float(r.average_price) if r.average_price is not None else None,
                "median": float(r.median_price) if r.median_price is not None else None,
                "avg_discount_pct": float(r.average_discount_pct)
                if r.average_discount_pct is not None
                else None,
                "products": r.product_count,
            }
            for r in brand_rows
        ]
    )

    c1, c2 = st.columns(2)
    with c1:
        bar_by_brand(
            pdf.dropna(subset=["average"]) if not pdf.empty else pdf,
            value_col="average",
            title="Average price by brand",
            definition="Latest priced observations by brand/currency",
            source="analytics.pricing.average_price_by_brand",
            filters_label=filters.label_summary(),
            hover_extra=["products", "currency"],
        )
    with c2:
        bar_by_brand(
            pdf.dropna(subset=["median"]) if not pdf.empty else pdf,
            value_col="median",
            title="Median price by brand",
            definition="Median latest price by brand/currency",
            source="analytics.pricing.median_price_by_brand",
            filters_label=filters.label_summary(),
            hover_extra=["products", "currency"],
        )

    bar_by_brand(
        pdf.dropna(subset=["avg_discount_pct"]) if not pdf.empty else pdf,
        value_col="avg_discount_pct",
        title="Average discount by brand",
        definition="Mean discount_pct for brands with discount observations",
        source="analytics.pricing.average_price_by_brand",
        filters_label=filters.label_summary(),
    )

    price_ts = price_change_over_time(session, scope=scope)
    disc_ts = discount_change_over_time(session, scope=scope)
    ts_price = pd.DataFrame(
        [
            {
                "date": p.period_start,
                "currency": p.currency,
                "average_price": float(p.average_price) if p.average_price is not None else None,
            }
            for p in price_ts
        ]
    )
    ts_disc = pd.DataFrame(
        [
            {
                "date": p.period_start,
                "currency": p.currency,
                "average_discount_pct": float(p.average_discount_pct)
                if p.average_discount_pct is not None
                else None,
            }
            for p in disc_ts
        ]
    )
    c3, c4 = st.columns(2)
    with c3:
        time_series(
            ts_price.dropna(subset=["average_price"]) if not ts_price.empty else ts_price,
            y="average_price",
            title="Price trend over time",
            source="analytics.pricing.price_change_over_time",
            filters_label=filters.label_summary(),
        )
    with c4:
        time_series(
            ts_disc.dropna(subset=["average_discount_pct"]) if not ts_disc.empty else ts_disc,
            y="average_discount_pct",
            title="Discount trend over time",
            source="analytics.pricing.discount_change_over_time",
            filters_label=filters.label_summary(),
        )

    def dim_df(rows):
        return pd.DataFrame(
            [
                {
                    "dimension": r.dimension,
                    "value": r.value,
                    "currency": r.currency,
                    "average_price": float(r.average_price) if r.average_price is not None else None,
                    "median_price": float(r.median_price) if r.median_price is not None else None,
                    "products": r.product_count,
                }
                for r in rows
            ]
        )

    st.subheader("Retailer / Country / Product-type comparison")
    t1, t2, t3 = st.tabs(["Retailer", "Country", "Product type"])
    with t1:
        show_dataframe(dim_df(compare_by_retailer(session, scope=scope)))
    with t2:
        show_dataframe(dim_df(compare_by_country(session, scope=scope)))
    with t3:
        show_dataframe(dim_df(compare_by_product_type(session, scope=scope)))

    st.subheader("Product pricing table")
    table = pd.DataFrame(
        [
            {
                "product_id": o.product_id,
                "brand": o.brand,
                "retailer": o.retailer_code,
                "country": o.country_code,
                "product_type": o.product_type,
                "current_price": float(o.current_price) if o.current_price is not None else None,
                "original_price": float(o.original_price) if o.original_price is not None else None,
                "discount_pct": float(o.discount_pct) if o.discount_pct is not None else None,
                "promotion": o.promotion_text,
                "currency": o.currency,
                "timestamp": o.observed_at,
            }
            for o in obs
        ]
    )
    show_dataframe(table, empty_message="No priced products for selected filters.")

    if not table.empty:
        pid = st.selectbox(
            "Drilldown — price history for product_id",
            options=["(select)"] + [str(x) for x in sorted(table["product_id"].unique())],
        )
        if pid != "(select)":
            detail = product_detail(session, int(pid))
            hist = pd.DataFrame(
                [
                    {
                        "observed_at": p.observed_at,
                        "price": float(p.price_amount) if p.price_amount is not None else None,
                        "list_price": float(p.list_price) if p.list_price is not None else None,
                        "discount_pct": float(p.discount_pct) if p.discount_pct is not None else None,
                        "currency": p.currency,
                        "on_promo": p.is_on_promotion,
                    }
                    for p in detail.get("prices", [])
                ]
            )
            show_dataframe(hist, empty_message="No price history for this product.")
