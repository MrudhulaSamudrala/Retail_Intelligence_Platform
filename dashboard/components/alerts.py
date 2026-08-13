"""Alert list rendering."""

from __future__ import annotations

import streamlit as st

from dashboard.services import AlertItem


def render_alerts(alerts: list[AlertItem]) -> None:
    st.markdown('<div class="ci-section-title">Alerts</div>', unsafe_allow_html=True)
    if not alerts:
        st.caption("No alert conditions met for current filters/thresholds.")
        return
    for a in alerts:
        cls = {
            "critical": "ci-alert-critical",
            "warning": "ci-alert-warning",
            "info": "ci-alert-info",
        }.get(a.severity, "ci-alert-info")
        st.markdown(
            f"""
            <div class="ci-alert {cls}">
                <strong>{a.title}</strong><br/>
                <span class="ci-muted">{a.detail}</span><br/>
                <span class="ci-trace">Source: {a.source}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
