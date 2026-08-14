"""Badge Coverage section."""

from __future__ import annotations

from datetime import datetime

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.layout import card, section_header, subtle_note
from dashboard.filters import DashboardFilters
from dashboard.presentation import TRACKED_PLATFORM_BRANDS
from dashboard.queries.catalog import badge_coverage_matrix
from dashboard.queries.collection import CollectionStatusSnapshot


_STATE_CLASS = {
    "GOOD": "ci-cell-good",
    "PARTIAL": "ci-cell-partial",
    "LOW": "ci-cell-low",
    "N/A": "ci-cell-na",
}


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del filters, collection, refreshed_at
    with card():
        section_header(
            "Badge Coverage",
            "Are brand badges being shown consistently?",
        )
        rows = badge_coverage_matrix(session)
        by_brand: dict[str, list[dict]] = {b: [] for b in TRACKED_PLATFORM_BRANDS}
        for row in rows:
            by_brand.setdefault(row["brand"], []).append(row)

        tables = []
        for brand in TRACKED_PLATFORM_BRANDS:
            items = by_brand.get(brand) or []
            if not items:
                continue
            head = "".join(f"<th>{item['badge']}</th>" for item in items)
            cells = []
            for item in items:
                cls = _STATE_CLASS.get(item["state"], "ci-cell-na")
                label = item["display"]
                cells.append(
                    f'<td class="{cls}">{label}<br>'
                    f'<span style="font-size:0.68rem">{item["state"]}</span></td>'
                )
            tables.append(
                f'<table class="ci-matrix"><thead><tr><th></th>{head}</tr></thead>'
                f'<tbody><tr><td class="ci-cell-brand">{brand}</td>{"".join(cells)}</tr></tbody></table>'
            )
        if tables:
            st.markdown("<div style='display:grid;gap:0.85rem'>" + "".join(tables) + "</div>", unsafe_allow_html=True)
        else:
            st.caption("No badge evidence in this collection.")
        subtle_note("GOOD ≥ 80% · PARTIAL 50–79% · LOW < 50% · N/A — No badge evidence")
