"""Banner Tracking section — presentation only (no analytics formula changes)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional, Sequence

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from analytics.banner_share import load_banner_observations
from dashboard.components.charts import horizontal_share_bars
from dashboard.components.filters_ui import select_retailer
from dashboard.components.kpi_cards import kpi_card_html
from dashboard.components.layout import card, empty_state, insight_card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters, to_banner_scope
from dashboard.presentation import TRACKED_PLATFORM_BRANDS, brand_sort_key, retailer_label
from dashboard.queries.collection import CollectionStatusSnapshot, filter_option_values
from dashboard.services import banner_share_by_brand
from dashboard.utils.semantics import MetricValue

UNKNOWN_OR_AMBIGUOUS = frozenset({"UNKNOWN", "AMBIGUOUS"})
BANNER_SHARE_EXPLANATION = (
    "Tracked-brand share among banners with reliable Intel, AMD, Qualcomm, or "
    "Apple evidence. UNKNOWN and AMBIGUOUS banners are excluded from the brand share."
)
NO_TRACKED_BRAND_EVIDENCE = "No tracked-brand banner evidence observed."
NO_TRACKED_BRAND_INSIGHT = (
    "No tracked-brand banner evidence was observed in this collection."
)
OBSERVATIONS_EXPANDER_LABEL = "View banner observations"


def banner_tracking_available(observation_count: int) -> bool:
    """Missing homepage banners are unavailable, not a 0% brand share."""
    return observation_count > 0


def tracked_brand_banner_shares(shares) -> list:
    """Chart only tracked brands that were actually observed."""
    return [
        s
        for s in shares
        if getattr(s, "brand", None) in TRACKED_PLATFORM_BRANDS
        and int(getattr(s, "banner_count", 0) or 0) > 0
    ]


def unknown_ambiguous_count(rows: Sequence[Any]) -> int:
    """Count UNKNOWN and AMBIGUOUS from banner observations, not from the chart."""
    return sum(
        1
        for row in rows
        if (getattr(row, "brand_detected", None) or "UNKNOWN") in UNKNOWN_OR_AMBIGUOUS
    )


@dataclass(frozen=True)
class BannerKpiCounts:
    total: int
    linked: int
    discounted: int
    badged: int
    unknown_or_ambiguous: int


def banner_kpi_counts(rows: Sequence[Any]) -> BannerKpiCounts:
    """KPI totals from the scoped observation list. Badge stays 0 when none exist."""
    linked = discounted = badged = 0
    for row in rows:
        if getattr(row, "link_present", False):
            linked += 1
        if getattr(row, "discount_text", None):
            discounted += 1
        if getattr(row, "badge_text", None):
            badged += 1
    return BannerKpiCounts(
        total=len(rows),
        linked=linked,
        discounted=discounted,
        badged=badged,
        unknown_or_ambiguous=unknown_ambiguous_count(rows),
    )


def highest_tracked_brand_insight(
    tracked_shares: Sequence[Any],
    *,
    retailer_code: Optional[str] = None,
) -> str:
    """Dynamic insight from observed tracked-brand shares. Never hardcodes a brand."""
    if not tracked_shares:
        return NO_TRACKED_BRAND_INSIGHT
    top_share = max(float(getattr(s, "banner_share", 0) or 0) for s in tracked_shares)
    tied = [
        s
        for s in tracked_shares
        if float(getattr(s, "banner_share", 0) or 0) == top_share
    ]
    tied = sorted(tied, key=lambda s: brand_sort_key(str(getattr(s, "brand", ""))))
    location = f" on {retailer_label(retailer_code)}" if retailer_code else ""
    if len(tied) == 1:
        return (
            f"{tied[0].brand} has the highest observed tracked-brand homepage "
            f"presence{location}."
        )
    names = " and ".join(str(s.brand) for s in tied)
    return (
        f"{names} have the highest observed tracked-brand homepage presence{location}."
    )


def banner_observation_records(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Detail rows for the expandable observations section."""
    records: list[dict[str, Any]] = []
    for row in reversed(list(rows)):
        records.append(
            {
                "Brand": getattr(row, "brand_detected", None) or "UNKNOWN",
                "Retailer": retailer_label(getattr(row, "retailer_code", None)),
                "Headline": getattr(row, "headline_text", None) or "",
                "Discount": "Yes" if getattr(row, "discount_text", None) else "No",
                "Link": "Yes" if getattr(row, "link_present", False) else "No",
                "Badge": "Yes" if getattr(row, "badge_text", None) else "No",
            }
        )
    return records


def _render_banner_kpis(counts: BannerKpiCounts) -> None:
    cards = [
        ("Total Banners", MetricValue.from_number(counts.total, display=str(counts.total))),
        ("Linked", MetricValue.from_number(counts.linked, display=str(counts.linked))),
        (
            "Discount",
            MetricValue.from_number(counts.discounted, display=str(counts.discounted)),
        ),
        ("Badge", MetricValue.from_number(counts.badged, display=str(counts.badged))),
        (
            "Unknown / Ambiguous",
            MetricValue.from_number(
                counts.unknown_or_ambiguous,
                display=str(counts.unknown_or_ambiguous),
            ),
        ),
    ]
    inner = "".join(kpi_card_html(label, metric) for label, metric in cards)
    st.markdown(f'<div class="ci-banner-kpis">{inner}</div>', unsafe_allow_html=True)


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
    with card():
        section_header(
            "Homepage Presence",
            "Which brands are getting homepage exposure?",
        )
        options = filter_option_values(session)
        retailer = select_retailer("banner_retailer", options.get("retailer_code", []))
        scoped = replace(filters, retailer_code=retailer)
        scope = to_banner_scope(scoped)
        snap = banner_share_by_brand(session, scope=scope)
        rows = list(load_banner_observations(session, scope=scope))

        if not banner_tracking_available(snap.total_observations):
            empty_state(
                "Banner tracking not available yet",
                "No homepage banner observations have been collected for the selected period.",
            )
            return

        counts = banner_kpi_counts(rows)
        _render_banner_kpis(counts)

        tracked_shares = tracked_brand_banner_shares(snap.shares)
        if not tracked_shares:
            empty_state(
                NO_TRACKED_BRAND_EVIDENCE,
                "UNKNOWN is not the same as 0% brand presence. These banners are excluded from tracked-brand share.",
            )
        else:
            df = pd.DataFrame(
                [
                    {
                        "brand": s.brand,
                        "share_pct": float(s.banner_share) * 100.0,
                        "banner_count": s.banner_count,
                    }
                    for s in tracked_shares
                ]
            )
            df["_ord"] = df["brand"].map(brand_sort_key)
            df = df.sort_values("_ord").drop(columns=["_ord"])
            horizontal_share_bars(
                df,
                category_col="brand",
                value_col="share_pct",
                title="Banner Share by Brand",
                definition="",
                source="analytics.banner_share.banner_share_by_brand",
                filters_label=scoped.label_summary(),
                x_title="Banner share (%)",
                hover_extra=["banner_count"],
                value_is_pct=True,
                height=240,
                empty_title=NO_TRACKED_BRAND_EVIDENCE,
                empty_explanation="UNKNOWN is not the same as 0% brand presence.",
            )

        insight_card(
            highest_tracked_brand_insight(tracked_shares, retailer_code=retailer)
        )

        with st.expander(OBSERVATIONS_EXPANDER_LABEL):
            show_dataframe(
                pd.DataFrame(banner_observation_records(rows)),
                empty_message="No banner observations",
                empty_explanation="Banner observations have not been collected yet.",
                height=320,
            )
