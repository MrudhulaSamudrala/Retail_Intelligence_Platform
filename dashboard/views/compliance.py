"""Brand Compliance page."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.charts import compliance_donut
from dashboard.components.header import render_header
from dashboard.components.kpi_cards import render_kpi_card
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.services import (
    _compliance_for_filters,
    compute_brand_scores,
    compute_country_scores,
    compute_retailer_scores,
    load_compliance_score_config,
)
from dashboard.utils.format import fmt_pct
from dashboard.utils.semantics import MetricValue
from database.models import RetailerAudit
from sqlalchemy import select


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="Brand Compliance",
        subtitle="S1–P5 audit scoring. UNKNOWN is never treated as FAIL. Notebook=85%, Desktop=15%.",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )

    rows, score = _compliance_for_filters(session, filters)
    cfg = load_compliance_score_config()

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        render_kpi_card(
            "Overall score",
            MetricValue.from_number(
                score.overall_score,
                display=fmt_pct(score.overall_score) if score.overall_score is not None else "N/A / insufficient data",
                source="analytics.compliance",
                definition="notebook×0.85 + desktop×0.15",
            ),
            timestamp=refreshed_at,
        )
    with c2:
        nb = score.notebook.score if score.notebook else None
        render_kpi_card(
            "Notebook score",
            MetricValue.from_number(nb, display=fmt_pct(nb) if nb is not None else "N/A / insufficient data"),
            timestamp=refreshed_at,
        )
    with c3:
        dt = score.desktop.score if score.desktop else None
        render_kpi_card(
            "Desktop score",
            MetricValue.from_number(dt, display=fmt_pct(dt) if dt is not None else "N/A / insufficient data"),
            timestamp=refreshed_at,
        )
    with c4:
        render_kpi_card(
            "PASS count",
            MetricValue.from_number(score.coverage.pass_count, display=str(score.coverage.pass_count)),
            timestamp=refreshed_at,
        )
    with c5:
        render_kpi_card(
            "FAIL count",
            MetricValue.from_number(score.coverage.fail_count, display=str(score.coverage.fail_count)),
            timestamp=refreshed_at,
        )
    with c6:
        render_kpi_card(
            "UNKNOWN count",
            MetricValue.from_number(score.coverage.unknown_count, display=str(score.coverage.unknown_count)),
            timestamp=refreshed_at,
        )

    st.caption(
        f"Total evaluated: {score.coverage.total_count} · "
        f"Scored (PASS+FAIL): {score.coverage.scored_count} · "
        f"Strategy: {score.check_aggregation_strategy or getattr(cfg, 'strategy', None)}"
    )

    st.subheader("S1–P5 breakdown")
    check_rows = []
    check_map: dict[str, float | None] = {}
    for code in ["S1", "S2", "P1", "P2", "P3", "P4", "P5"]:
        # Aggregate coverage across notebook+desktop check scores
        pass_c = fail_c = unk_c = 0
        scores = []
        for seg in (score.notebook, score.desktop):
            if seg is None:
                continue
            cs = seg.check_scores.get(code)
            if cs is None:
                continue
            pass_c += cs.coverage.pass_count
            fail_c += cs.coverage.fail_count
            unk_c += cs.coverage.unknown_count
            if cs.score is not None:
                scores.append(cs.score)
        avg = sum(scores) / len(scores) if scores else None
        check_map[code] = avg
        status = "NO DATA"
        if pass_c + fail_c + unk_c == 0:
            status = "NO DATA"
        elif pass_c + fail_c == 0 and unk_c > 0:
            status = "UNKNOWN"
        elif fail_c > 0 and pass_c == 0:
            status = "FAIL"
        elif fail_c == 0 and pass_c > 0:
            status = "PASS"
        else:
            status = "MIXED"
        check_rows.append(
            {
                "check": code,
                "status": status,
                "pass": pass_c,
                "fail": fail_c,
                "unknown": unk_c,
                "pass_rate": fmt_pct(avg) if avg is not None else "N/A",
            }
        )
    show_dataframe(pd.DataFrame(check_rows))
    compliance_donut(check_map, filters_label=filters.label_summary())

    st.subheader("Brand / Retailer / Country scores")
    brand_scores = compute_brand_scores(rows, config=cfg)
    retailer_scores = compute_retailer_scores(rows, config=cfg)
    country_scores = compute_country_scores(rows, config=cfg)

    def score_table(mapping, key_name: str) -> pd.DataFrame:
        out = []
        for key, sc in mapping.items():
            out.append(
                {
                    key_name: key,
                    "overall": fmt_pct(sc.overall_score) if sc.overall_score is not None else "N/A / insufficient data",
                    "notebook": fmt_pct(sc.notebook.score) if sc.notebook and sc.notebook.score is not None else "N/A",
                    "desktop": fmt_pct(sc.desktop.score) if sc.desktop and sc.desktop.score is not None else "N/A",
                    "pass": sc.coverage.pass_count,
                    "fail": sc.coverage.fail_count,
                    "unknown": sc.coverage.unknown_count,
                }
            )
        return pd.DataFrame(out)

    t1, t2, t3 = st.tabs(["Brand", "Retailer", "Country"])
    with t1:
        show_dataframe(score_table(brand_scores, "brand"))
    with t2:
        show_dataframe(score_table(retailer_scores, "retailer"))
    with t3:
        show_dataframe(score_table(country_scores, "country"))

    st.subheader("Product-level audit drilldown")
    stmt = select(RetailerAudit).order_by(RetailerAudit.observed_at.desc()).limit(500)
    audits = list(session.scalars(stmt).all())
    # Apply filters compatible with audit rows
    filtered = []
    for a in audits:
        if filters.retailer_code and a.retailer_code != filters.retailer_code:
            continue
        if filters.country_code and a.country_code != filters.country_code:
            continue
        if filters.product_type and a.product_type != filters.product_type:
            continue
        if filters.brand and a.brand != filters.brand:
            continue
        filtered.append(a)

    drill = pd.DataFrame(
        [
            {
                "product_id": a.product_id,
                "brand": a.brand,
                "retailer": a.retailer_code,
                "country": a.country_code,
                "product_type": a.product_type,
                "check": a.check_code,
                "result": a.result,
                "reason": (
                    (a.details.get("reason") if isinstance(a.details, dict) else None)
                    or a.evidence_text
                ),
                "observed_at": a.observed_at,
            }
            for a in filtered
        ]
    )
    show_dataframe(drill, empty_message="No audit records for selected filters.")
    st.caption(
        "When result=UNKNOWN, reason often reflects blocked/unavailable evidence "
        "(e.g. PDP account verification). UNKNOWN ≠ FAIL."
    )
