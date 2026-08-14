"""Product Identity supporting section."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from analytics.product_identity import MATCHED
from analytics.product_identity.queries import product_availability_matrix
from dashboard.components.kpi_cards import render_kpi_card
from dashboard.components.layout import card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import MATCHED_STATUS, crosswalk_summary
from dashboard.utils.semantics import MetricValue
import streamlit as st


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del filters, collection
    with card():
        section_header(
            "Product Identity",
            "Cross-retailer matches. Conservative matches are MATCHED only.",
        )
        summary = crosswalk_summary(session)
        matched = summary.get("common_products", 0)
        unmatched = summary.get("unmatched", 0)
        conservative = summary.get("matched", 0)

        c1, c2, c3 = st.columns(3)
        with c1:
            render_kpi_card(
                "Matched Products",
                MetricValue.from_number(matched, display=str(matched), source="analytics.product_identity"),
                timestamp=refreshed_at,
            )
        with c2:
            render_kpi_card(
                "Unmatched Products",
                MetricValue.from_number(unmatched, display=str(unmatched), source="analytics.product_identity"),
                timestamp=refreshed_at,
            )
        with c3:
            render_kpi_card(
                "Conservative Matches",
                MetricValue.from_number(
                    conservative,
                    display=str(conservative),
                    source="analytics.product_identity",
                    definition=f"match_status={MATCHED_STATUS}",
                ),
                timestamp=refreshed_at,
            )

        matrix = product_availability_matrix(session)
        pairs = [row for row in matrix if row.on_newegg and row.on_mercadolibre]
        pairs.sort(key=lambda r: (0 if r.match_status == MATCHED else 1, r.display_name or ""))
        table = pd.DataFrame(
            [
                {
                    "Product": r.display_name or r.manufacturer_model or f"canonical:{r.canonical_product_id}",
                    "Retailer A": "Newegg" if r.on_newegg else "—",
                    "Retailer B": "Mercado Libre" if r.on_mercadolibre else "—",
                    "Match Type": r.match_status,
                }
                for r in pairs[:12]
            ]
        )
        show_dataframe(
            table,
            empty_message="No cross-retailer pairs",
            empty_explanation="There are no products currently linked across both retailers.",
            height=260,
        )
