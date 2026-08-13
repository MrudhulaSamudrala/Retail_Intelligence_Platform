"""Visibility page — retailer-specific, cross-retailer MATCHED, and brand SoV."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session

from dashboard.components.header import render_header
from dashboard.components.kpi_cards import render_kpi_card
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters, to_sov_scope
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import (
    MATCHED_STATUS,
    crosswalk_summary,
    highest_cross_retailer_visibility,
    highest_visibility_by_retailer,
    keyword_metrics,
    list_cross_retailer_visibility,
    share_of_voice,
)
from dashboard.utils.semantics import MetricValue
from database.models import SearchObservation


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="Visibility",
        subtitle="Retailer-specific product visibility, MATCHED cross-retailer visibility, and brand Share of Voice.",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )

    sov_scope = to_sov_scope(filters)
    sov = share_of_voice(session, scope=sov_scope)

    if sov.partial_searches > 0 or sov.collection_basis in {"observed_partial", "mixed"}:
        st.warning(
            "Partial search coverage — this is not an exact full-SERP Share of Voice measurement."
        )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card(
            "Tracked appearances",
            MetricValue.from_number(
                sov.tracked_appearances,
                display=str(sov.tracked_appearances),
                source="analytics.share_of_voice",
            ),
            timestamp=refreshed_at,
        )
    with c2:
        render_kpi_card(
            "COMPLETE searches",
            MetricValue.from_number(sov.complete_searches, display=str(sov.complete_searches)),
            timestamp=refreshed_at,
        )
    with c3:
        render_kpi_card(
            "PARTIAL searches",
            MetricValue.from_number(sov.partial_searches, display=str(sov.partial_searches)),
            timestamp=refreshed_at,
        )
    with c4:
        render_kpi_card(
            "Collection basis",
            MetricValue.from_number(
                None if not sov.collection_basis else 1,
                display=sov.collection_basis or "No data",
                source="analytics.share_of_voice",
            ),
            timestamp=refreshed_at,
        )

    st.markdown("### A. Retailer-specific visibility")
    st.caption("Highest visibility products — never mixes retailers.")
    for retailer in ("newegg", "mercadolibre"):
        if filters.retailer_code and filters.retailer_code != retailer:
            continue
        st.markdown(f"#### {retailer}")
        rows = highest_visibility_by_retailer(
            session,
            retailer,
            country_code=filters.country_code,
            brand=filters.brand,
            product_type=filters.product_type,
            observed_from=filters.date_from,
            observed_to=filters.date_to,
            top_n=20,
        )
        df = pd.DataFrame(
            [
                {
                    "product_id": r.product_id,
                    "sku": r.retailer_sku,
                    "title": r.title,
                    "brand": r.brand,
                    "oem": r.oem,
                    "appearances": r.appearances,
                    "top3": r.top3_appearances,
                    "top5": r.top5_appearances,
                    "top10": r.top10_appearances,
                    "top20": r.top20_appearances,
                    "avg_rank": float(r.average_rank) if r.average_rank is not None else None,
                    "visibility_score": float(r.visibility_score),
                    "keywords": ", ".join(r.keywords),
                }
                for r in rows
            ]
        )
        show_dataframe(df, empty_message=f"No visibility data for {retailer}.")

    st.markdown("### B. Cross-retailer visibility (MATCHED only)")
    st.caption(
        "Only products with reliable canonical MATCHED identity. "
        "POSSIBLE_MATCH / UNMATCHED excluded from authoritative ranking."
    )
    include_possible = st.checkbox(
        "Also show POSSIBLE_MATCH (not authoritative)",
        value=False,
        key="vis_possible",
    )
    xwalk = crosswalk_summary(session)
    st.write({"crosswalk_summary": xwalk, "authoritative_status": MATCHED_STATUS})

    matched_rows = list_cross_retailer_visibility(session, top_n=50)
    if include_possible:
        st.info(
            "Authoritative table remains MATCHED-only. "
            "POSSIBLE_MATCH is not included in combined rankings by design."
        )

    xdf = pd.DataFrame(
        [
            {
                "canonical_product_id": r.canonical_product_id,
                "display_name": r.display_name,
                "match_status": r.match_status,
                "match_method": r.match_method,
                "match_confidence": float(r.match_confidence)
                if r.match_confidence is not None
                else None,
                "newegg_product_id": r.newegg_product_id,
                "mercadolibre_product_id": r.mercadolibre_product_id,
                "newegg_visibility": float(r.newegg_visibility.visibility_score)
                if r.newegg_visibility
                else None,
                "mercadolibre_visibility": float(r.mercadolibre_visibility.visibility_score)
                if r.mercadolibre_visibility
                else None,
                "combined_visibility": float(r.combined_visibility_score),
                "combined_appearances": r.combined_appearances,
            }
            for r in matched_rows
        ]
    )
    if xdf.empty:
        st.info(
            "No MATCHED cross-retailer pairs with visibility data for the selected filters."
        )
    else:
        show_dataframe(xdf)

    top_combined = highest_cross_retailer_visibility(session, top_n=10)
    st.subheader("Highest combined visibility (MATCHED)")
    show_dataframe(
        pd.DataFrame(
            [
                {
                    "canonical_product_id": r.canonical_product_id,
                    "name": r.display_name,
                    "combined_score": float(r.combined_visibility_score),
                    "match_confidence": float(r.match_confidence)
                    if r.match_confidence is not None
                    else None,
                    "method": r.match_method,
                }
                for r in top_combined
            ]
        ),
        empty_message="Insufficient MATCHED identity for cross-retailer ranking.",
    )

    st.markdown("### Search Visibility / Share of Voice")
    brand_df = pd.DataFrame(
        [
            {
                "brand": m.brand,
                "present": m.present,
                "appearances": m.appearances,
                "top_n": m.top_n,
                "top_n_count": m.top_n_count,
                "average_rank": float(m.average_rank) if m.average_rank is not None else None,
                "share_of_voice_pct": float(m.share_of_voice) * 100.0,
                "basis": m.collection_basis,
            }
            for m in sov.metrics
        ]
    )
    show_dataframe(brand_df, empty_message="No Share of Voice metrics for selected filters.")

    st.subheader("Keyword table")
    kw = keyword_metrics(session, scope=sov_scope)
    kw_df = pd.DataFrame(
        [
            {
                "brand": m.brand,
                "keyword": m.keyword,
                "retailer": m.retailer_code,
                "country": m.country_code,
                "appearances": m.appearances,
                "top_n_count": m.top_n_count,
                "avg_rank": float(m.average_rank) if m.average_rank is not None else None,
                "sov_pct": float(m.share_of_voice) * 100.0,
                "basis": m.collection_basis,
            }
            for m in kw
        ]
    )
    show_dataframe(kw_df, empty_message="No keyword metrics.")

    st.subheader("Search collection quality")
    statuses = session.execute(
        select(SearchObservation.collection_status, SearchObservation.retailer_code)
        .order_by(SearchObservation.observed_at.desc())
        .limit(500)
    ).all()
    quality_rows = [{"retailer": retailer, "collection_status": status} for status, retailer in statuses]
    if quality_rows:
        qdf = (
            pd.DataFrame(quality_rows)
            .value_counts(["retailer", "collection_status"])
            .reset_index(name="count")
        )
        show_dataframe(qdf)
        if ((qdf["retailer"] == "mercadolibre") & (qdf["collection_status"] == "PARTIAL")).any():
            st.warning(
                "Mercado Libre search is PARTIAL — this is not an exact full-SERP "
                "Share of Voice measurement."
            )
    else:
        st.info("No search observations available.")
