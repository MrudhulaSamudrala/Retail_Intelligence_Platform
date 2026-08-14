"""Reusable Plotly chart helpers — restrained, presentation-ready."""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.components.layout import empty_state

BAR_COLOR = "#0070F3"
PLOTLY_CONFIG = {
    "displayModeBar": False,
    "responsive": True,
}


def _text_color() -> str:
    try:
        if str(st.get_option("theme.base")).lower() == "dark":
            return "#fafafa"
    except Exception:
        pass
    return "#171717"


def _muted_color() -> str:
    try:
        if str(st.get_option("theme.base")).lower() == "dark":
            return "#a3a3a3"
    except Exception:
        pass
    return "#737373"


def _grid_color() -> str:
    try:
        if str(st.get_option("theme.base")).lower() == "dark":
            return "#262626"
    except Exception:
        pass
    return "#efefef"


def _apply_layout(fig: go.Figure, *, height: int, x_title: str, y_title: str = "") -> None:
    text = _text_color()
    muted = _muted_color()
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(
            family="Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
            size=13,
            color=text,
        ),
        margin=dict(l=4, r=16, t=8, b=8),
        height=height,
        showlegend=False,
        bargap=0.38,
        hoverlabel=dict(
            bgcolor="#111111",
            font_size=12,
            font_family="Inter, sans-serif",
            font_color="#fafafa",
        ),
        xaxis=dict(
            title=dict(text=x_title, font=dict(size=12, color=muted)),
            showgrid=True,
            gridcolor=_grid_color(),
            zeroline=False,
            tickfont=dict(size=12, color=muted),
            linecolor="rgba(0,0,0,0)",
        ),
        yaxis=dict(
            title=dict(text=y_title, font=dict(size=12, color=muted)) if y_title else None,
            showgrid=False,
            automargin=True,
            tickfont=dict(size=13, color=text),
            linecolor="rgba(0,0,0,0)",
        ),
    )


def chart_caption(*, metric: str, definition: str, source: str, filters_label: str, denominator: Optional[str] = None) -> None:
    parts = [definition, f"Filters: {filters_label}"]
    if denominator:
        parts.append(f"Denominator: {denominator}")
    st.caption(" · ".join(parts))


def empty_chart(
    message: str = "No data available for the selected filters.",
    *,
    explanation: str = "There is nothing to plot for the current collection and filters.",
) -> None:
    empty_state(message, explanation)


def horizontal_share_bars(
    df: pd.DataFrame,
    *,
    category_col: str,
    value_col: str,
    title: str,
    definition: str,
    source: str,
    filters_label: str,
    x_title: str,
    hover_extra: Optional[Sequence[str]] = None,
    value_is_pct: bool = False,
    empty_title: str = "No data available",
    empty_explanation: str = "There is nothing to plot for the current collection and filters.",
    height: Optional[int] = None,
) -> None:
    if df is None or df.empty:
        empty_chart(empty_title, explanation=empty_explanation)
        return
    work = df.dropna(subset=[value_col]).copy()
    if work.empty:
        empty_chart(empty_title, explanation=empty_explanation)
        return
    work = work.sort_values(value_col, ascending=True)
    labels = work[category_col].astype(str).tolist()
    values = work[value_col].tolist()
    custom = None
    extra_cols = [c for c in (hover_extra or []) if c in work.columns]
    if extra_cols:
        custom = work[extra_cols].to_numpy()

    if value_is_pct:
        hover = "%{y}: %{x:.1f}%<extra></extra>"
        tickformat = ".1f"
        suffix = "%"
    else:
        hover = "%{y}: %{x:,.2f}<extra></extra>"
        tickformat = ",.2f"
        suffix = ""

    fig = go.Figure(
        data=[
            go.Bar(
                x=values,
                y=labels,
                orientation="h",
                marker=dict(color=BAR_COLOR, line=dict(width=0)),
                hovertemplate=hover,
                customdata=custom,
            )
        ]
    )
    fig_height = height if height is not None else max(220, 32 * len(work) + 48)
    _apply_layout(fig, height=fig_height, x_title=x_title)
    fig.update_xaxes(ticksuffix=suffix if value_is_pct else "", tickformat=tickformat)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    chart_caption(metric=title, definition=definition, source=source, filters_label=filters_label)


# Back-compat names used by unused explorer pages — keep no-op wrappers thin.
def bar_by_brand(df: pd.DataFrame, *, value_col: str, title: str, definition: str, source: str, filters_label: str, hover_extra=None) -> None:
    horizontal_share_bars(
        df,
        category_col="brand",
        value_col=value_col,
        title=title,
        definition=definition,
        source=source,
        filters_label=filters_label,
        x_title=title,
        hover_extra=hover_extra,
        value_is_pct="pct" in value_col or "share" in value_col,
    )


def line_sos_trend(df: pd.DataFrame, *, filters_label: str, partial: bool = False) -> None:
    del df, filters_label, partial
    empty_chart("Trend not shown", explanation="This dashboard uses a single current Share of Shelf view.")


def stacked_bar_sos_by_retailer(df: pd.DataFrame, *, mode: str, filters_label: str) -> None:
    del df, mode, filters_label
    empty_chart("Retailer breakdown not shown", explanation="Use the retailer filter on Share of Shelf instead.")


def _compliance_ring_figure(ring: dict) -> go.Figure:
    from dashboard.presentation import CHECK_CODES, status_color

    brand = str(ring.get("brand") or "")
    overall = ring.get("overall")
    checks = list(ring.get("checks") or [])
    by_code = {c["code"]: c for c in checks}
    ordered = [
        by_code.get(code, {"code": code, "status": "UNKNOWN", "pass": 0, "fail": 0, "unknown": 0})
        for code in CHECK_CODES
    ]
    colors = [status_color(str(item.get("status") or "UNKNOWN")) for item in ordered]
    custom = []
    for item in ordered:
        p, f, u = int(item.get("pass", 0)), int(item.get("fail", 0)), int(item.get("unknown", 0))
        total = p + f + u
        coverage = f"{p + f}/{total}" if total else "—"
        custom.append([brand, item["code"], item.get("status"), p, f, u, coverage])
    if overall is None:
        subtitle = str(ring.get("center_subtitle") or "No Data")
        center = f"<b>N/A</b><br><span style='font-size:12px;color:#737373'>{subtitle}</span>"
    else:
        subtitle = str(ring.get("center_subtitle") or "Pass Rate")
        center = (
            f"<b>{float(overall)*100:.0f}%</b><br>"
            f"<span style='font-size:12px;color:#737373'>{subtitle}</span>"
        )
    fig = go.Figure(
        data=[
            go.Pie(
                labels=[item["code"] for item in ordered],
                values=[1] * len(ordered),
                hole=0.58,
                sort=False,
                direction="clockwise",
                rotation=90,
                marker=dict(colors=colors, line=dict(color="#ffffff", width=2)),
                text=[item["code"] for item in ordered],
                textinfo="text",
                textposition="inside",
                insidetextorientation="horizontal",
                textfont=dict(size=11, color="#171717"),
                customdata=custom,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "%{customdata[1]} — %{customdata[2]}<br>"
                    "PASS: %{customdata[3]}<br>"
                    "FAIL: %{customdata[4]}<br>"
                    "UNKNOWN: %{customdata[5]}<br>"
                    "Coverage: %{customdata[6]}"
                    "<extra></extra>"
                ),
                showlegend=False,
                domain=dict(x=[0.12, 0.88], y=[0.08, 0.88]),
            )
        ]
    )
    fig.update_layout(
        autosize=True,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=36, b=8),
        height=340,
        title=dict(
            text=brand.upper(),
            font=dict(size=13, color=_text_color()),
            x=0.5,
            xanchor="center",
            y=0.98,
            yanchor="top",
        ),
        font=dict(family="Inter, sans-serif", size=11, color=_text_color()),
        annotations=[
            dict(
                text=center,
                x=0.5,
                y=0.48,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=18, color=_text_color()),
            )
        ],
        hoverlabel=dict(bgcolor="#111111", font_size=12, font_color="#fafafa"),
        uniformtext_minsize=10,
        uniformtext_mode="show",
    )
    return fig


def compliance_donut(rings: Sequence[dict], *, filters_label: str) -> None:
    """Canonical brand-compliance visualization: one ring per brand, seven check segments.

    Segment size is equal so color (PASS/FAIL/UNKNOWN) carries the meaning.
    Center text is the existing overall score, or N/A when that score is missing.
    """
    if not rings:
        empty_chart(
            "N/A — No data",
            explanation="No brand compliance rings to display for this collection.",
        )
        return

    for row_start in range(0, len(rings), 2):
        cols = st.columns(2, gap="large")
        row = list(rings[row_start : row_start + 2])
        while len(row) < 2:
            row.append(None)
        for col, ring in zip(cols, row):
            with col:
                if ring is None:
                    continue
                fig = _compliance_ring_figure(ring)
                st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    st.caption(
        "PASS green · FAIL red · UNKNOWN gray. Center uses analytics.compliance "
        "(overall = notebook × 0.85 + desktop × 0.15 when both segments exist; "
        "otherwise the available segment score). N/A only when the brand has no scored PASS/FAIL. "
        f"Filters: {filters_label}. Source: `analytics.compliance`."
    )


def time_series(df: pd.DataFrame, *, y: str, title: str, source: str, filters_label: str) -> None:
    del df, y, title, source, filters_label
    empty_chart("Time series not shown", explanation="Pricing is shown as average price by brand.")
