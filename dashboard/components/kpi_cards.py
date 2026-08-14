"""KPI card rendering."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Optional, Sequence

import streamlit as st

from dashboard.utils.semantics import DataState, MetricValue


def kpi_card_html(
    label: str,
    metric: MetricValue,
) -> str:
    tip_parts = []
    if metric.definition:
        tip_parts.append(metric.definition)
    if metric.source:
        tip_parts.append(f"Source: {metric.source}")
    if metric.denominator is not None:
        tip_parts.append(f"Denominator: {metric.denominator}")
    if metric.detail:
        tip_parts.append(metric.detail)
    tip = html.escape(" | ".join(tip_parts).replace('"', "'"))

    value_class = "value"
    if metric.display == "COMPLETE":
        value_class = "value ci-val-ok"
    elif metric.display == "PARTIAL":
        value_class = "value ci-val-warn"

    meta = ""
    if metric.detail:
        meta = f'<div class="meta">{html.escape(str(metric.detail))}</div>'
    elif metric.state in {
        DataState.PARTIAL,
        DataState.BLOCKED,
        DataState.UNKNOWN,
        DataState.INSUFFICIENT,
        DataState.NO_DATA,
    }:
        meta = f'<div class="meta">{html.escape(metric.state.value.replace("_", " ").title())}</div>'

    return (
        f'<div class="ci-kpi" title="{tip}">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="{value_class}">{html.escape(str(metric.display))}</div>'
        f"{meta}</div>"
    )


def render_kpi_card(
    label: str,
    metric: MetricValue,
    *,
    delta_label: Optional[str] = None,
    delta_direction: Optional[str] = None,
    timestamp: Optional[datetime] = None,
) -> None:
    del delta_label, delta_direction, timestamp
    st.markdown(kpi_card_html(label, metric), unsafe_allow_html=True)


def render_kpi_row(cards: Sequence[tuple[str, MetricValue]]) -> None:
    inner = "".join(kpi_card_html(label, metric) for label, metric in cards)
    st.markdown(f'<div class="ci-kpi-row">{inner}</div>', unsafe_allow_html=True)


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
