"""Executive Overview KPI row and key takeaways."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from dashboard.components.kpi_cards import render_kpi_row
from dashboard.components.layout import section_header, takeaway_cards
from dashboard.filters import DashboardFilters
from dashboard.insights_text import (
    compliance_takeaway,
    search_takeaway,
    shelf_takeaway,
)
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import (
    metric_average_discount,
    metric_share_of_shelf,
    metric_share_of_voice,
    retailer_search_coverage,
    tracked_brand_names,
)
from dashboard.utils.semantics import DataState, MetricValue
from dashboard.views.compliance import scored_checks_by_brand


def _status_metric(coverage) -> MetricValue:
    if coverage.status == "UNAVAILABLE":
        return MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
        )
    if coverage.status == "PARTIAL":
        return MetricValue(
            state=DataState.PARTIAL,
            display="PARTIAL",
            detail=coverage.headline,
        )
    if coverage.status == "COMPLETE":
        return MetricValue(
            state=DataState.OK,
            value=1,
            display="COMPLETE",
            detail=coverage.headline,
        )
    return MetricValue(
        state=DataState.NO_DATA,
        display=coverage.status,
        detail=coverage.detail,
    )


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
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
        )
    else:
        eligible = MetricValue.from_number(
            sos_snap.universe_size,
            display=str(sos_snap.universe_size),
            detail="Gaming products in current collection",
        )

    if sov_snap.collection_basis in {"empty", "NO_DATA"} and sov_snap.total_observations == 0:
        serp = MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
        )
    else:
        serp = MetricValue.from_number(
            sov_snap.total_observations,
            display=str(sov_snap.total_observations),
            detail="Observed product positions",
        )

    if disc.state == DataState.NO_DATA:
        disc = MetricValue(
            state=DataState.NO_DATA,
            display="N/A",
            detail="No data",
        )
    else:
        disc = MetricValue(
            state=disc.state,
            value=disc.value,
            display=disc.display,
            detail="Across discounted products",
            denominator=disc.denominator,
            numerator=disc.numerator,
        )

    render_kpi_row(
        [
            ("Tracked Products", eligible),
            ("SERP Positions", serp),
            (
                "Tracked Brands",
                MetricValue.from_number(
                    len(brands),
                    display=str(len(brands)),
                    detail=" · ".join(brands),
                ),
            ),
            ("Avg. Discount", disc),
            ("Newegg Collection", _status_metric(newegg)),
            ("Mercado Libre Collection", _status_metric(ml)),
        ]
    )

    weakest = scored_checks_by_brand(session, filters)
    section_header("Key Takeaways", "The competitive picture from the current collection.")
    takeaway_cards(
        [
            shelf_takeaway(sos_snap.shares),
            search_takeaway(sov_snap.metrics),
            compliance_takeaway(weakest),
        ]
    )
