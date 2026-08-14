"""Brand Compliance section — 2×2 rings plus weakest-check interpretation."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from analytics.compliance.config import CHECK_CODES
from analytics.compliance.models import CoverageStats
from dashboard.components.charts import compliance_donut
from dashboard.components.layout import card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.insights_text import compliance_gap_lines
from dashboard.presentation import (
    TRACKED_PLATFORM_BRANDS,
    brand_score_lookup,
    check_visual_status,
    displayed_compliance_center,
    format_check_cell,
    format_coverage_cell,
)
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import _compliance_for_filters, compute_brand_scores, load_compliance_score_config


def _merged_check_coverage(score, code: str) -> CoverageStats:
    merged = CoverageStats()
    segments = [score.notebook, score.desktop]
    segments.extend(score.other_segments.values())
    for seg in segments:
        if seg is None:
            continue
        cs = seg.check_scores.get(code)
        if cs is None:
            continue
        merged.pass_count += cs.coverage.pass_count
        merged.fail_count += cs.coverage.fail_count
        merged.unknown_count += cs.coverage.unknown_count
    return merged


def scored_checks_by_brand(session: Session, filters: DashboardFilters) -> dict[str, list[tuple[str, float]]]:
    """Lowest-to-highest scored check pass rates per brand. Existing scoring only."""
    rows, _score = _compliance_for_filters(session, filters)
    cfg = load_compliance_score_config()
    brand_scores = compute_brand_scores(rows, config=cfg)
    weakest: dict[str, list[tuple[str, float]]] = {}
    for brand in TRACKED_PLATFORM_BRANDS:
        sc = brand_score_lookup(brand_scores, brand)
        ranked: list[tuple[str, float]] = []
        for code in CHECK_CODES:
            cov = _merged_check_coverage(sc, code) if sc is not None else CoverageStats()
            if cov.scored_count > 0 and cov.pass_rate is not None:
                ranked.append((code, float(cov.pass_rate)))
        ranked.sort(key=lambda item: item[1])
        weakest[brand] = ranked
    return weakest


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
    with card():
        section_header(
            "Brand Compliance",
            "Where brand presentation is passing, failing, or still missing evidence.",
        )
        rows, _score = _compliance_for_filters(session, filters)
        cfg = load_compliance_score_config()
        brand_scores = compute_brand_scores(rows, config=cfg)

        rings = []
        table_rows = []
        weakest: dict[str, list[tuple[str, float]]] = {}
        for brand in TRACKED_PLATFORM_BRANDS:
            sc = brand_score_lookup(brand_scores, brand)
            checks = []
            table_row = {"Brand": brand}
            ranked: list[tuple[str, float]] = []
            for code in CHECK_CODES:
                cov = _merged_check_coverage(sc, code) if sc is not None else CoverageStats()
                status = check_visual_status(cov.pass_count, cov.fail_count, cov.unknown_count)
                checks.append(
                    {
                        "code": code,
                        "status": status if cov.total_count else "UNKNOWN",
                        "pass": cov.pass_count,
                        "fail": cov.fail_count,
                        "unknown": cov.unknown_count,
                    }
                )
                table_row[code] = format_check_cell(cov.pass_count, cov.fail_count, cov.unknown_count)
                if cov.scored_count > 0 and cov.pass_rate is not None:
                    ranked.append((code, float(cov.pass_rate)))
            ranked.sort(key=lambda item: item[1])
            weakest[brand] = ranked
            center_score, center_subtitle = displayed_compliance_center(sc)
            if sc is None:
                table_row["Coverage"] = "—"
            else:
                table_row["Coverage"] = format_coverage_cell(
                    sc.coverage.pass_count,
                    sc.coverage.fail_count,
                    sc.coverage.unknown_count,
                )
            rings.append(
                {
                    "brand": brand,
                    "overall": center_score,
                    "center_subtitle": center_subtitle,
                    "checks": checks,
                }
            )
            table_rows.append(table_row)

        compliance_donut(rings, filters_label=filters.label_summary())

        st.markdown("**Where is compliance being lost?**")
        blocks = []
        for brand in TRACKED_PLATFORM_BRANDS:
            lines = compliance_gap_lines(weakest.get(brand) or [])
            body = "".join(f"<li>{line}</li>" for line in lines)
            blocks.append(f"<div><h4>{brand}</h4><ul>{body}</ul></div>")
        st.markdown(f'<div class="ci-weak">{"".join(blocks)}</div>', unsafe_allow_html=True)

        with st.expander("Methodology"):
            st.caption(
                "Each ring has seven checks: S1 listing title, S2 listing badge, "
                "P1 product title, P2 product badge, P3 spec table, P4 brand media, "
                "P5 OEM media. Green is PASS, red is FAIL, gray is NO DATA. "
                "N/A is not 0%. Center uses the existing compliance score when available."
            )
            show_dataframe(
                pd.DataFrame(table_rows),
                empty_message="N/A — No data",
                empty_explanation="No scored compliance checks for tracked brands.",
                height=260,
            )
