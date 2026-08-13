"""Sidebar navigation + collection status card."""

from __future__ import annotations

import streamlit as st

from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_ts

PAGES = [
    "Executive Overview",
    "Pricing & Promotions",
    "Share of Shelf",
    "Brand Compliance",
    "Visibility",
    "SKU Explorer",
    "Insights",
]


def render_sidebar(collection: CollectionStatusSnapshot) -> str:
    st.sidebar.markdown("### Navigation")
    page = st.sidebar.radio("Go to", PAGES, label_visibility="collapsed")

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Collection Status")
    st.sidebar.caption(collection.freshness_label)
    st.sidebar.write(f"**Latest run:** `{collection.latest_status}`")
    st.sidebar.write(f"**Run id:** {collection.latest_run_id or '—'}")
    st.sidebar.write(f"**Last successful:** {fmt_ts(collection.last_successful_at)}")
    st.sidebar.write(f"**Next scheduled:** {collection.next_scheduled_hint}")
    st.sidebar.write(f"**Frequency:** {collection.frequency}")

    if collection.components:
        st.sidebar.markdown("#### Components")
        for comp in collection.components:
            status = comp.status
            reason = f" — {comp.reason}" if comp.reason and status == "PARTIAL" else ""
            st.sidebar.write(f"**{comp.component}**: `{status}`{reason}")
    else:
        st.sidebar.caption("No component steps on latest run.")

    any_partial = any(c.status == "PARTIAL" for c in collection.components)
    any_failed = any(c.status == "FAILED" for c in collection.components)
    if any_failed:
        st.sidebar.error("Critical components FAILED — not all systems operational.")
    elif any_partial or collection.is_partial:
        st.sidebar.warning("One or more components PARTIAL — do not claim full coverage.")
    elif collection.is_live:
        st.sidebar.success("Latest successful collection is fresh.")

    return page
