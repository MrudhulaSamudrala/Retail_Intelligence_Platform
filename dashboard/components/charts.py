"""Reusable Plotly chart helpers with auditability captions."""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PALETTE = ["#1570ef", "#12b76a", "#f79009", "#7a5af8", "#ee46bc", "#6172f3", "#f04438"]


def chart_caption(
    *,
    metric: str,
    definition: str,
    source: str,
    filters_label: str,
    denominator: Optional[str] = None,
) -> None:
    parts = [
        f"**{metric}** — {definition}",
        f"Filters: {filters_label}",
        f"Source: `{source}`",
    ]
    if denominator:
        parts.append(f"Denominator: {denominator}")
    st.caption(" · ".join(parts))


def empty_chart(message: str = "No data available for the selected filters.") -> None:
    st.info(message)


def line_sos_trend(df: pd.DataFrame, *, filters_label: str, partial: bool = False) -> None:
    if df.empty:
        empty_chart()
        return
    if partial:
        st.warning("PARTIAL data — Share of Shelf trend may be incomplete.")
    fig = px.line(
        df,
        x="date",
        y="share_pct",
        color="brand",
        markers=True,
        color_discrete_sequence=PALETTE,
        hover_data=["eligible_count", "universe_size", "share_pct"],
        labels={"share_pct": "Share of Shelf %", "date": "Date", "brand": "Brand"},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        legend_title_text="Brand",
        height=360,
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    st.plotly_chart(fig, use_container_width=True)
    chart_caption(
        metric="Share of Shelf Trend",
        definition="Brand product count / eligible tracked product universe per day",
        source="analytics.share_of_shelf.share_of_shelf_trends",
        filters_label=filters_label,
        denominator="sos_universe_v1 eligible listings",
    )


def stacked_bar_sos_by_retailer(
    df: pd.DataFrame,
    *,
    mode: str,
    filters_label: str,
) -> None:
    if df.empty:
        empty_chart()
        return
    ycol = "share_pct" if mode == "percentage" else "product_count"
    ylabel = "Share %" if mode == "percentage" else "Product count"
    fig = px.bar(
        df,
        y="retailer",
        x=ycol,
        color="brand",
        orientation="h",
        color_discrete_sequence=PALETTE,
        labels={ycol: ylabel, "retailer": "Retailer", "brand": "Brand"},
    )
    fig.update_layout(
        barmode="stack",
        margin=dict(l=10, r=10, t=30, b=10),
        height=320,
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    st.plotly_chart(fig, use_container_width=True)
    chart_caption(
        metric="Share of Shelf by Retailer",
        definition="Brand composition of eligible universe by retailer",
        source="analytics.share_of_shelf_by_brand",
        filters_label=filters_label,
    )


def bar_by_brand(
    df: pd.DataFrame,
    *,
    value_col: str,
    title: str,
    definition: str,
    source: str,
    filters_label: str,
    hover_extra: Optional[Sequence[str]] = None,
) -> None:
    if df.empty:
        empty_chart()
        return
    fig = px.bar(
        df,
        x="brand",
        y=value_col,
        color="currency" if "currency" in df.columns else None,
        color_discrete_sequence=PALETTE,
        hover_data=list(hover_extra or []),
        labels={"brand": "Brand", value_col: title},
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=340,
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    st.plotly_chart(fig, use_container_width=True)
    chart_caption(
        metric=title,
        definition=definition,
        source=source,
        filters_label=filters_label,
    )


def compliance_donut(check_scores: dict[str, float | None], *, filters_label: str) -> None:
    labels = []
    values = []
    for code in ["S1", "S2", "P1", "P2", "P3", "P4", "P5"]:
        score = check_scores.get(code)
        if score is None:
            continue
        labels.append(code)
        values.append(score * 100.0)
    if not values:
        empty_chart("Insufficient data for S1–P5 breakdown.")
        return
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=PALETTE),
                textinfo="label+percent",
            )
        ]
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=340,
        showlegend=True,
        annotations=[
            dict(text="Checks", x=0.5, y=0.5, font_size=14, showarrow=False)
        ],
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Donut shows relative scored pass-rate weight visualization of available checks only. "
        "Overall score uses configured notebook 85% / desktop 15% methodology — not inventing check weights. "
        f"Filters: {filters_label}. Source: `analytics.compliance`."
    )


def time_series(df: pd.DataFrame, *, y: str, title: str, source: str, filters_label: str) -> None:
    if df.empty:
        empty_chart()
        return
    fig = px.line(
        df,
        x="date",
        y=y,
        color="currency" if "currency" in df.columns else None,
        markers=True,
        color_discrete_sequence=PALETTE,
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        height=340,
        plot_bgcolor="white",
        paper_bgcolor="white",
        title=title,
    )
    st.plotly_chart(fig, use_container_width=True)
    chart_caption(
        metric=title,
        definition="Daily aggregate from price_history / snapshots",
        source=source,
        filters_label=filters_label,
    )
