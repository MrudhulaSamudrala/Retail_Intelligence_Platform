"""Global page header."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional

import streamlit as st

from dashboard.components.layout import status_pill
from dashboard.filters import DashboardFilters
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_ts


def _to_dt(d: Optional[date], end: bool = False) -> Optional[datetime]:
    if d is None:
        return None
    t = time(23, 59, 59) if end else time(0, 0, 0)
    return datetime.combine(d, t, tzinfo=timezone.utc)


def render_header(
    *,
    page_title: str,
    subtitle: str,
    collection: CollectionStatusSnapshot,
    filters: DashboardFilters,
    analytics_refreshed_at: Optional[datetime],
) -> DashboardFilters:
    del analytics_refreshed_at
    if collection.latest_status in {"FAILED", "ERROR"}:
        badge_kind, badge_text = "bad", "Collection failed"
    elif collection.is_stale:
        badge_kind, badge_text = "warn", "Stale collection"
    elif collection.latest_run_id:
        badge_kind, badge_text = "ok", "Latest collection"
    else:
        badge_kind, badge_text = "muted", "No collection yet"

    left, right = st.columns([1.6, 1], gap="large")
    with left:
        st.markdown(
            """
            <div class="ci-kicker">BRIDGEAI</div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(f'<h1 class="ci-title">{page_title}</h1>', unsafe_allow_html=True)
        st.markdown(f'<p class="ci-subtitle">{subtitle}</p>', unsafe_allow_html=True)
        st.markdown(
            f"""
            {status_pill(badge_text, kind=badge_kind)}
            <span class="ci-note" style="display:inline;margin-left:0.6rem;">
            {fmt_ts(collection.latest_completed_at or collection.latest_started_at)}
            </span>
            """,
            unsafe_allow_html=True,
        )
    with right:
        c1, c2, c3 = st.columns([0.9, 1.6, 1.2])
        with c1:
            if st.button("Refresh", use_container_width=True):
                st.cache_data.clear()
                st.session_state["analytics_refreshed_at"] = datetime.now(timezone.utc)
                st.rerun()
        with c2:
            default_range = (
                filters.date_from.date() if filters.date_from else date.today(),
                filters.date_to.date() if filters.date_to else date.today(),
            )
            dates = st.date_input("Collection Period", value=default_range, key="collection_period")
        with c3:
            st.selectbox(
                "Time Zone",
                ["UTC", "India Standard Time", "Local"],
                key="display_timezone",
            )

    date_from = filters.date_from
    date_to = filters.date_to
    if isinstance(dates, tuple) and len(dates) == 2:
        date_from, date_to = _to_dt(dates[0], False), _to_dt(dates[1], True)
    elif isinstance(dates, date):
        date_from, date_to = _to_dt(dates, False), _to_dt(dates, True)

    from dataclasses import replace

    return replace(filters, date_from=date_from, date_to=date_to)
