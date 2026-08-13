"""KPI card rendering."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import streamlit as st

from dashboard.utils.format import fmt_ts
from dashboard.utils.semantics import DataState, MetricValue


def render_kpi_card(
    label: str,
    metric: MetricValue,
    *,
    delta_label: Optional[str] = None,
    delta_direction: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> None:
    delta_class = "delta-neu"
    if delta_direction == "up":
        delta_class = "delta-pos"
    elif delta_direction == "down":
        delta_class = "delta-neg"

    tip_parts = []
    if metric.definition:
        tip_parts.append(metric.definition)
    if metric.source:
        tip_parts.append(f"Source: {metric.source}")
    if metric.denominator is not None:
        tip_parts.append(f"Denominator: {metric.denominator}")
    if metric.detail:
        tip_parts.append(metric.detail)
    tip = " | ".join(tip_parts)

    state_note = ""
    if metric.state in {DataState.PARTIAL, DataState.BLOCKED, DataState.UNKNOWN, DataState.INSUFFICIENT, DataState.NO_DATA}:
        state_note = f'<div class="meta">{metric.state.value}</div>'

    delta_html = ""
    if delta_label:
        delta_html = f'<div class="{delta_class}">{delta_label}</div>'

    ts_html = f'<div class="meta">Data: {fmt_ts(timestamp)}</div>' if timestamp else ""

    st.markdown(
        f"""
        <div class="ci-kpi" title="{tip}">
            <div class="label">{label}</div>
            <div class="value">{metric.display}</div>
            {delta_html}
            {state_note}
            {ts_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def comparison_delta(current, previous, *, as_pp: bool = False, already_ratio: bool = True):
    from dashboard.utils.format import fmt_change

    delta, label = fmt_change(
        current, previous, as_pct_points=as_pp, already_ratio=already_ratio
    )
    direction = None
    if delta is None:
        return None, label if label == "Insufficient data" else None
    if delta > 0:
        direction = "up"
    elif delta < 0:
        direction = "down"
    else:
        direction = "flat"
    return direction, label
