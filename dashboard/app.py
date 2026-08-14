"""Retail Competitive Intelligence — Streamlit entrypoint.

Run from repo root:

    streamlit run dashboard/app.py
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from dashboard.components.header import render_header
from dashboard.components.theme import inject_theme
from dashboard.config import dashboard_meta
from dashboard.db import check_connection, read_session
from dashboard.filters import default_filters
from dashboard.queries.collection import load_collection_status
from dashboard.views import (
    attributes,
    badges,
    banners,
    compliance,
    overview,
    pricing,
    share_of_shelf,
    sku_explorer,
    visibility,
)

st.set_page_config(
    page_title=dashboard_meta().get("title", "Retail Competitive Intelligence"),
    page_icon="◇",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject_theme()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_collection_status_payload() -> dict:
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
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load dashboard metadata: {exc}")
        st.stop()

    if "analytics_refreshed_at" not in st.session_state:
        st.session_state["analytics_refreshed_at"] = datetime.now(timezone.utc)
    refreshed_at = st.session_state["analytics_refreshed_at"]
    filters = render_header(
        page_title="Competitive Intelligence",
        subtitle="Shelf presence, search visibility, pricing, promotions, and brand presentation across tracked retailers.",
        collection=collection,
        filters=default_filters(),
        analytics_refreshed_at=refreshed_at,
    )

    with read_session() as session:
        overview.render(session, filters, collection, refreshed_at)
        share_of_shelf.render(session, filters, collection, refreshed_at)
        visibility.render(session, filters)
        pricing.render(session, filters, collection, refreshed_at)
        compliance.render(session, filters, collection, refreshed_at)
        banners.render(session, filters, collection, refreshed_at)
        attributes.render(session, filters, collection, refreshed_at)
        badges.render(session, filters, collection, refreshed_at)
        sku_explorer.render(session, filters, collection, refreshed_at)


if __name__ == "__main__":
    main()
