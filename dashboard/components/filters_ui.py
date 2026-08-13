"""Global filter controls."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional

import streamlit as st

from dashboard.filters import DashboardFilters, default_filters


def _to_dt(d: Optional[date], end: bool = False) -> Optional[datetime]:
    if d is None:
        return None
    t = time(23, 59, 59) if end else time(0, 0, 0)
    return datetime.combine(d, t, tzinfo=timezone.utc)


def render_global_filters(options: dict[str, list[str]]) -> DashboardFilters:
    if st.session_state.pop("filters_clear", False):
        st.session_state.pop("filter_retailer", None)
        st.session_state.pop("filter_country", None)
        st.session_state.pop("filter_ptype", None)
        st.session_state.pop("filter_brand", None)
        st.session_state.pop("filter_oem", None)
        st.session_state.pop("filter_dates", None)

    defaults = default_filters()
    st.markdown("##### Filters")
    c1, c2, c3, c4, c5, c6 = st.columns(6)

    def _select(col, label: str, key: str, values: list[str]):
        with col:
            choices = ["(all)"] + values
            picked = st.selectbox(label, choices, key=key)
            return None if picked == "(all)" else picked

    retailer = _select(c1, "Retailer", "filter_retailer", options.get("retailer_code", []))
    country = _select(c2, "Country", "filter_country", options.get("country_code", []))
    ptype = _select(c3, "Product Type", "filter_ptype", options.get("product_type", []))
    brand = _select(c4, "Brand", "filter_brand", options.get("brand", []))
    oem = _select(c5, "OEM", "filter_oem", options.get("oem", []))

    with c6:
        default_range = (
            defaults.date_from.date() if defaults.date_from else date.today(),
            defaults.date_to.date() if defaults.date_to else date.today(),
        )
        dates = st.date_input(
            "Date Range",
            value=st.session_state.get("filter_dates", default_range),
            key="filter_dates_widget",
        )
        st.session_state["filter_dates"] = dates

    date_from = date_to = None
    if isinstance(dates, tuple) and len(dates) == 2:
        date_from, date_to = _to_dt(dates[0], False), _to_dt(dates[1], True)
    elif isinstance(dates, date):
        date_from = _to_dt(dates, False)
        date_to = _to_dt(dates, True)

    return DashboardFilters(
        retailer_code=retailer,
        country_code=country,
        product_type=ptype,
        brand=brand,
        oem=oem,
        date_from=date_from,
        date_to=date_to,
    )
