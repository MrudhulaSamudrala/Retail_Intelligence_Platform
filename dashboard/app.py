"""Retail Competitive Intelligence — Streamlit entrypoint.

Run from repo root:

    streamlit run dashboard/app.py

Page modules live in ``dashboard/views/`` (not ``pages/``) so Streamlit does not
auto-register them as multipage routes; navigation is owned by the sidebar.

Read-only: re-queries PostgreSQL / analytics. Does not start collectors.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from dashboard.components.filters_ui import render_global_filters
from dashboard.components.sidebar import render_sidebar
from dashboard.components.theme import inject_theme
from dashboard.config import dashboard_meta
from dashboard.db import check_connection, read_session
from dashboard.views import (
    compliance,
    insights,
    overview,
    pricing,
    share_of_shelf,
    sku_explorer,
    visibility,
)
from dashboard.queries.collection import filter_option_values, load_collection_status


st.set_page_config(
    page_title=dashboard_meta().get("title", "Retail Competitive Intelligence"),
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

inject_theme()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_filter_options() -> dict[str, list[str]]:
    with read_session() as session:
        return filter_option_values(session)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_collection_status_payload() -> dict:
    """Cache a serializable snapshot; reconstruct light object in UI."""
    with read_session() as session:
        snap = load_collection_status(session)
        return {
            "latest_run_id": snap.latest_run_id,
            "latest_status": snap.latest_status,
            "latest_started_at": snap.latest_started_at,
            "latest_completed_at": snap.latest_completed_at,
            "last_successful_at": snap.last_successful_at,
            "retailers": snap.retailers,
            "is_partial": snap.is_partial,
            "is_stale": snap.is_stale,
            "is_live": snap.is_live,
            "freshness_label": snap.freshness_label,
            "next_scheduled_hint": snap.next_scheduled_hint,
            "frequency": snap.frequency,
            "components": [
                {
                    "component": c.component,
                    "status": c.status,
                    "completed_at": c.completed_at,
                    "records_processed": c.records_processed,
                    "error_message": c.error_message,
                    "details": c.details,
                }
                for c in snap.components
            ],
        }


def _collection_from_payload(payload: dict):
    from dashboard.queries.collection import CollectionStatusSnapshot, ComponentStatus

    return CollectionStatusSnapshot(
        latest_run_id=payload["latest_run_id"],
        latest_status=payload["latest_status"],
        latest_started_at=payload["latest_started_at"],
        latest_completed_at=payload["latest_completed_at"],
        last_successful_at=payload["last_successful_at"],
        retailers=list(payload["retailers"] or []),
        is_partial=payload["is_partial"],
        is_stale=payload["is_stale"],
        is_live=payload["is_live"],
        freshness_label=payload["freshness_label"],
        next_scheduled_hint=payload["next_scheduled_hint"],
        frequency=payload["frequency"],
        components=[ComponentStatus(**c) for c in payload["components"]],
    )


def main() -> None:
    ok, msg = check_connection()
    if not ok:
        st.error(f"Cannot connect to PostgreSQL: {msg}")
        st.stop()

    try:
        collection = _collection_from_payload(_cached_collection_status_payload())
        options = _cached_filter_options()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load dashboard metadata: {exc}")
        st.stop()

    page = render_sidebar(collection)
    filters = render_global_filters(options)

    if "analytics_refreshed_at" not in st.session_state:
        st.session_state["analytics_refreshed_at"] = datetime.now(timezone.utc)
    refreshed_at = st.session_state["analytics_refreshed_at"]

    renderers = {
        "Executive Overview": overview.render,
        "Pricing & Promotions": pricing.render,
        "Share of Shelf": share_of_shelf.render,
        "Brand Compliance": compliance.render,
        "Visibility": visibility.render,
        "SKU Explorer": sku_explorer.render,
        "Insights": insights.render,
    }

    with read_session() as session:
        renderers[page](session, filters, collection, refreshed_at)


if __name__ == "__main__":
    main()
