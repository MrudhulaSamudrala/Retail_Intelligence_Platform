"""Global page header with freshness indicators."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from dashboard.filters import DashboardFilters
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_ts


def render_header(
    *,
    page_title: str,
    subtitle: str,
    collection: CollectionStatusSnapshot,
    filters: DashboardFilters,
    analytics_refreshed_at: Optional[datetime],
) -> None:
    left, right = st.columns([3, 2])
    with left:
        st.markdown("### Retail Competitive Intelligence")
        st.markdown(f"## {page_title}")
        st.caption(subtitle)
    with right:
        if collection.is_partial:
            badge_class, badge_text = "ci-badge-partial", "PARTIAL DATA"
        elif collection.is_live:
            badge_class, badge_text = "ci-badge-live", "Live / Latest successful collection"
        elif collection.is_stale:
            badge_class, badge_text = "ci-badge-stale", "Stale data"
        else:
            badge_class, badge_text = "ci-badge-info", collection.freshness_label

        st.markdown(
            f'<span class="ci-badge {badge_class}">{badge_text}</span>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"Last successful collection: **{fmt_ts(collection.last_successful_at)}**  \n"
            f"Latest run status: **{collection.latest_status}**  \n"
            f"Retailer(s): **{', '.join(collection.retailers) or '—'}**  \n"
            f"Selected range: **{filters.label_summary()}**"
        )
        st.caption(
            f"Analytics refresh: {fmt_ts(analytics_refreshed_at)} "
            "(re-query only — does not run collectors)"
        )

    c1, c2, _ = st.columns([1, 1, 4])
    with c1:
        if st.button("Refresh analytics", use_container_width=True):
            st.cache_data.clear()
            st.session_state["analytics_refreshed_at"] = datetime.now(timezone.utc)
            st.rerun()
    with c2:
        if st.button("Clear filters", use_container_width=True):
            st.session_state["filters_clear"] = True
            st.rerun()
    st.divider()
