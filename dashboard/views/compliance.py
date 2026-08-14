"""Brand Compliance section — summary, brand cards, check table, gaps."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import streamlit as st
from sqlalchemy.orm import Session

from analytics.compliance.config import CHECK_CODES
from analytics.compliance.models import CoverageStats
from dashboard.components.charts import PLOTLY_CONFIG, _compliance_ring_figure
from dashboard.components.layout import card, section_header
from dashboard.filters import DashboardFilters
from dashboard.presentation import (
    CHECK_LABELS,
    TRACKED_PLATFORM_BRANDS,
    brand_score_lookup,
    check_visual_status,
    displayed_compliance_center,
    format_center_percent,
    format_check_status_cell,
    format_coverage_cell,
    lowest_scored_checks,
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


@dataclass
class BrandComplianceCard:
    brand: str
    center_score: Optional[float]
    center_subtitle: str
    pass_checks: int
    fail_checks: int
    coverage_label: str
    has_scored_checks: bool
    ring: dict
    check_rows: list[dict] = field(default_factory=list)
    ranked: list[tuple[str, float]] = field(default_factory=list)
    lowest: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class CompliancePresentation:
    overall_display: str
    overall_subtitle: str
    pass_count: int
    fail_count: int
    unknown_count: int
    coverage_label: str
    coverage_pct: str
    has_observations: bool
    brands: list[BrandComplianceCard]


def build_compliance_presentation(overall_score, brand_scores: dict) -> CompliancePresentation:
    """Assemble UI fields from existing analytics objects. No new scoring."""
    center, subtitle = displayed_compliance_center(overall_score)
    cov = getattr(overall_score, "coverage", None) if overall_score is not None else None
    if cov is None:
        cov = CoverageStats()
    has_observations = cov.total_count > 0
    if has_observations:
        coverage_label = f"{cov.scored_count} / {cov.total_count}"
        coverage_pct = (
            f"{cov.coverage_rate * 100:.0f}%" if cov.coverage_rate is not None else "—"
        )
    else:
        coverage_label = "—"
        coverage_pct = "—"

    brands: list[BrandComplianceCard] = []
    for brand in TRACKED_PLATFORM_BRANDS:
        sc = brand_score_lookup(brand_scores, brand)
        checks = []
        check_rows = []
        ranked: list[tuple[str, float]] = []
        pass_checks = 0
        fail_checks = 0
        for code in CHECK_CODES:
            check_cov = _merged_check_coverage(sc, code) if sc is not None else CoverageStats()
            status = check_visual_status(
                check_cov.pass_count, check_cov.fail_count, check_cov.unknown_count
            )
            if not check_cov.total_count:
                status = "UNKNOWN"
            if status == "PASS":
                pass_checks += 1
            elif status == "FAIL":
                fail_checks += 1
            cell_text, cell_status = format_check_status_cell(
                check_cov.pass_count, check_cov.fail_count, check_cov.unknown_count
            )
            checks.append(
                {
                    "code": code,
                    "status": status,
                    "pass": check_cov.pass_count,
                    "fail": check_cov.fail_count,
                    "unknown": check_cov.unknown_count,
                }
            )
            check_rows.append(
                {
                    "code": code,
                    "label": CHECK_LABELS.get(code, code),
                    "cell": cell_text,
                    "status": cell_status,
                    "pass_rate": check_cov.pass_rate,
                }
            )
            if check_cov.scored_count > 0 and check_cov.pass_rate is not None:
                ranked.append((code, float(check_cov.pass_rate)))
        ranked.sort(key=lambda item: item[1])
        center_score, center_subtitle = displayed_compliance_center(sc)
        if sc is None or sc.coverage.total_count <= 0:
            coverage_brand = "—"
        else:
            coverage_brand = format_coverage_cell(
                sc.coverage.pass_count,
                sc.coverage.fail_count,
                sc.coverage.unknown_count,
            )
        brands.append(
            BrandComplianceCard(
                brand=brand,
                center_score=center_score,
                center_subtitle=center_subtitle,
                pass_checks=pass_checks,
                fail_checks=fail_checks,
                coverage_label=coverage_brand,
                has_scored_checks=bool(ranked),
                ring={
                    "brand": brand,
                    "overall": center_score,
                    "center_subtitle": center_subtitle,
                    "checks": checks,
                },
                check_rows=check_rows,
                ranked=ranked,
                lowest=lowest_scored_checks(ranked, limit=3),
            )
        )

    return CompliancePresentation(
        overall_display=format_center_percent(center),
        overall_subtitle=subtitle,
        pass_count=cov.pass_count,
        fail_count=cov.fail_count,
        unknown_count=cov.unknown_count,
        coverage_label=coverage_label,
        coverage_pct=coverage_pct,
        has_observations=has_observations,
        brands=brands,
    )


def _kpi_card(label: str, value: str, meta: str) -> str:
    return (
        '<div class="ci-kpi">'
        f'<div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div>'
        f'<div class="meta">{html.escape(meta)}</div>'
        "</div>"
    )


def _render_kpis(model: CompliancePresentation) -> None:
    if model.has_observations:
        pass_meta = "scored observations"
        fail_meta = "scored observations"
        unknown_meta = "UNKNOWN excluded from rates"
        coverage_meta = model.coverage_pct
        pass_value = f"{model.pass_count} checks"
        fail_value = f"{model.fail_count} checks"
        unknown_value = f"{model.unknown_count} checks"
    else:
        pass_meta = "No Data"
        fail_meta = "No Data"
        unknown_meta = "No Data"
        coverage_meta = "No Data"
        pass_value = "—"
        fail_value = "—"
        unknown_value = "—"
    inner = "".join(
        [
            _kpi_card("Overall", model.overall_display, model.overall_subtitle),
            _kpi_card("PASS", pass_value, pass_meta),
            _kpi_card("FAIL", fail_value, fail_meta),
            _kpi_card("NO DATA", unknown_value, unknown_meta),
            _kpi_card("Coverage", model.coverage_label, coverage_meta),
        ]
    )
    st.markdown(f'<div class="ci-comp-kpis">{inner}</div>', unsafe_allow_html=True)


def _brand_header_html(card_model: BrandComplianceCard) -> str:
    return f'<div class="ci-comp-brand-name">{html.escape(card_model.brand.upper())}</div>'


def _brand_stats_html(card_model: BrandComplianceCard) -> str:
    if not card_model.has_scored_checks:
        return (
            '<div class="ci-comp-stats">'
            '<div class="ci-comp-stat ci-comp-na">PASS —</div>'
            '<div class="ci-comp-stat ci-comp-na">FAIL —</div>'
            '<div class="ci-comp-stat ci-comp-na">Coverage —</div>'
            '<div class="ci-comp-stat ci-comp-na">No scored checks</div>'
            "</div>"
        )
    return (
        '<div class="ci-comp-stats">'
        f'<div class="ci-comp-stat ci-comp-pass">✓ {card_model.pass_checks} PASS</div>'
        f'<div class="ci-comp-stat ci-comp-fail">✕ {card_model.fail_checks} FAIL</div>'
        f'<div class="ci-comp-stat">Coverage {html.escape(card_model.coverage_label)}</div>'
        "</div>"
    )


def _render_brand_cards(model: CompliancePresentation) -> None:
    brands = model.brands
    for row_start in range(0, len(brands), 2):
        cols = st.columns(2, gap="medium")
        pair = brands[row_start : row_start + 2]
        for col, card_model in zip(cols, pair):
            with col:
                st.markdown(
                    f'<div class="ci-comp-brand">{_brand_header_html(card_model)}</div>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(
                    _compliance_ring_figure(card_model.ring),
                    use_container_width=True,
                    config=PLOTLY_CONFIG,
                )
                st.markdown(_brand_stats_html(card_model), unsafe_allow_html=True)
                if card_model.has_scored_checks:
                    with st.expander("View checks"):
                        for row in card_model.check_rows:
                            st.caption(f"{row['code']} · {row['label']} · {row['cell']}")


def _cell_class(status: str) -> str:
    if status == "PASS":
        return "ci-cell-good"
    if status == "FAIL":
        return "ci-cell-low"
    return "ci-cell-na"


def _render_check_table(model: CompliancePresentation) -> None:
    st.markdown("**Compliance Check Details**")
    header = "".join(
        f"<th>{html.escape(title)}</th>"
        for title in ["Check", "Description", *(b.brand for b in model.brands)]
    )
    body_rows = []
    for code in CHECK_CODES:
        cells = [f"<td>{html.escape(code)}</td>", f"<td>{html.escape(CHECK_LABELS.get(code, code))}</td>"]
        for brand in model.brands:
            row = next(item for item in brand.check_rows if item["code"] == code)
            cells.append(
                f'<td class="{_cell_class(row["status"])}">{html.escape(row["cell"])}</td>'
            )
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    st.markdown(
        '<div class="ci-comp-table-wrap">'
        f'<table class="ci-matrix ci-comp-table"><thead><tr>{header}</tr></thead>'
        f"<tbody>{''.join(body_rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _loss_item_html(code: str, rate: float, status: str) -> str:
    label = CHECK_LABELS.get(code, code)
    kind = "pass" if status == "PASS" else "fail" if status == "FAIL" else "na"
    mark = "🟢" if kind == "pass" else "🔴" if kind == "fail" else "●"
    return (
        f'<div class="ci-comp-loss-item ci-comp-{kind}">'
        f"{mark} {html.escape(code)} — {html.escape(label)} · {rate * 100:.0f}%"
        "</div>"
    )


def _render_loss(model: CompliancePresentation) -> None:
    st.markdown("**Where is compliance being lost?**")
    blocks = []
    for brand in model.brands:
        if not brand.has_scored_checks:
            body = '<div class="ci-comp-stat ci-comp-na">No scored checks available.</div>'
        else:
            items = []
            status_by_code = {row["code"]: row["status"] for row in brand.check_rows}
            for code, rate in brand.lowest:
                items.append(_loss_item_html(code, rate, status_by_code.get(code, "UNKNOWN")))
            body = "".join(items)
        blocks.append(
            '<div class="ci-comp-loss-card">'
            f'<div class="ci-comp-brand-name">{html.escape(brand.brand.upper())}</div>'
            f"{body}</div>"
        )
    st.markdown(f'<div class="ci-comp-loss">{"".join(blocks)}</div>', unsafe_allow_html=True)


def _render_legend() -> None:
    st.markdown(
        '<div class="ci-legend">'
        '<span class="ci-legend-chip ci-legend-pass">✓ PASS</span>'
        '<span class="ci-legend-chip ci-legend-fail">✕ FAIL</span>'
        '<span class="ci-legend-chip ci-legend-na">● NO DATA</span>'
        '<span class="ci-legend-note">Coverage = scored observations / eligible observations</span>'
        "</div>",
        unsafe_allow_html=True,
    )


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
            "Where brand presentation is passing, failing, or missing evidence across the 7 compliance checks.",
        )
        rows, overall_score = _compliance_for_filters(session, filters)
        cfg = load_compliance_score_config()
        brand_scores = compute_brand_scores(rows, config=cfg)
        model = build_compliance_presentation(overall_score, brand_scores)

        _render_kpis(model)
        _render_brand_cards(model)
        _render_legend()
        _render_check_table(model)
        _render_loss(model)

        with st.expander("How is Brand Compliance calculated?"):
            nb = cfg.segment_weights.get("notebook")
            dt = cfg.segment_weights.get("desktop")
            st.caption(
                "7 checks: S1 · S2 · P1 · P2 · P3 · P4 · P5. "
                "UNKNOWN is excluded from PASS/FAIL rates. "
                "N/A means no scored evidence exists. "
                f"Overall score uses the existing notebook ({nb}) / desktop ({dt}) weighting."
            )
            st.caption(
                "Each ring has seven equal segments: S1 listing title, S2 listing badge, "
                "P1 product title, P2 product badge, P3 spec table, P4 brand media, "
                "P5 OEM media. Green is PASS, red is FAIL, gray is NO DATA. "
                "N/A is not 0%. Center uses the existing compliance score when available."
            )
