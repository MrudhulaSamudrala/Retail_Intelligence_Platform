"""Executive Overview KPI row."""

from __future__ import annotations

from datetime import datetime

import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.kpi_cards import render_kpi_row
from dashboard.filters import DashboardFilters
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import (
    metric_average_discount,
    metric_share_of_shelf,
    metric_share_of_voice,
    retailer_search_coverage,
    tracked_brand_names,
)
from dashboard.utils.semantics import DataState, MetricValue


def _status_metric(coverage) -> MetricValue:
    if coverage.status == "UNAVAILABLE":
        return MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
            source="analytics.share_of_voice",
            definition="Search collection completeness from Share of Voice snapshot",
        )
    if coverage.status == "PARTIAL":
        return MetricValue(
            state=DataState.PARTIAL,
            display="PARTIAL",
            detail=coverage.headline,
            source="analytics.share_of_voice",
            definition="Search collection completeness from Share of Voice snapshot",
        )
    if coverage.status == "COMPLETE":
        return MetricValue(
            state=DataState.OK,
            value=1,
            display="COMPLETE",
            detail=coverage.headline,
            source="analytics.share_of_voice",
            definition="All configured strata observed without fallback",
        )
    return MetricValue(
        state=DataState.NO_DATA,
        display=coverage.status,
        detail=coverage.detail,
        source="analytics.share_of_voice",
    )


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection
    _, sos_snap = metric_share_of_shelf(session, filters)
    _, sov_snap = metric_share_of_voice(session, filters)
    disc = metric_average_discount(session, filters)
    brands = tracked_brand_names()
    newegg = retailer_search_coverage(session, "newegg")
    ml = retailer_search_coverage(session, "mercadolibre")

    if sos_snap.collection_status == "NO_DATA" and sos_snap.universe_size == 0:
        eligible = MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
            source="analytics.share_of_shelf",
            definition="Eligible gaming products in the Share of Shelf universe",
        )
    else:
        eligible = MetricValue.from_number(
            sos_snap.universe_size,
            display=str(sos_snap.universe_size),
            denominator=sos_snap.universe_size,
            source="analytics.share_of_shelf",
            definition="Eligible gaming products in the Share of Shelf universe (sos_universe_v1)",
            detail="Tracked gaming universe",
        )

    if sov_snap.collection_basis in {"empty", "NO_DATA"} and sov_snap.total_observations == 0:
        serp = MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
            source="analytics.share_of_voice",
            definition="Native SERP positions in the latest stratified search batch",
        )
    else:
        serp = MetricValue.from_number(
            sov_snap.total_observations,
            display=str(sov_snap.total_observations),
            source="analytics.share_of_voice",
            definition="Native SERP positions in the latest stratified search batch",
            detail="Across all retailers",
        )

    if disc.state == DataState.NO_DATA:
        disc = MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
            source=disc.source,
            definition=disc.definition,
        )
    elif not disc.detail:
        disc = MetricValue(
            state=disc.state,
            value=disc.value,
            display=disc.display,
            detail="On discounted products",
            source=disc.source,
            definition=disc.definition,
            denominator=disc.denominator,
            numerator=disc.numerator,
        )

    render_kpi_row(
        [
            ("Eligible Products", eligible),
            ("Observed SERP Positions", serp),
            (
                "Tracked Brands",
                MetricValue.from_number(
                    len(brands),
                    display=str(len(brands)),
                    source="config/keywords.yaml",
                    definition="Intel, AMD, Qualcomm, Apple",
                    detail=", ".join(brands),
                ),
            ),
            ("Average Discount", disc),
            ("Newegg Collection", _status_metric(newegg)),
            ("Mercado Libre Collection", _status_metric(ml)),
        ]
    )
