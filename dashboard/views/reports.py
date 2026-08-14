"""Reports — download existing Excel/PSV exports. Does not regenerate."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from dashboard.components.layout import empty_state, pill_kind_for_status, section_header
from dashboard.db import read_session
from dashboard.queries.reports import (
    DiscoveredReport,
    attach_run_metadata,
    discover_reports,
    split_latest_and_previous,
)

_CSS = """
<style>
.ci-report-info {
    border: 1px solid var(--ci-border);
    border-radius: var(--ci-radius);
    background: var(--ci-ok-bg);
    padding: 0.7rem 0.9rem;
    margin: 0 0 1rem 0;
    color: var(--ci-muted);
    font-size: 0.84rem;
    line-height: 1.45;
}
.ci-report-kicker {
    font-size: 0.72rem;
    font-weight: 650;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ci-faint);
    margin: 0 0 0.55rem 0;
}
.ci-report-latest {
    background: var(--ci-card);
    border: 1px solid var(--ci-border);
    border-radius: 12px;
    box-shadow: var(--ci-shadow);
    padding: 1rem 1.1rem 0.35rem 1.1rem;
    margin: 0 0 0.35rem 0;
}
.ci-report-latest-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 0.45rem;
}
.ci-report-run {
    font-size: 1.35rem;
    font-weight: 650;
    letter-spacing: -0.03em;
    color: var(--ci-text);
}
.ci-report-latest .ci-pill-ok { color: #166534; border-color: #bbf7d0; background: #ecfdf5; }
.ci-report-card .ci-pill-ok { color: #166534; border-color: #bbf7d0; background: #ecfdf5; }
.ci-report-meta {
    color: var(--ci-muted);
    font-size: 0.88rem;
    line-height: 1.45;
    margin: 0.15rem 0;
}
.ci-report-metrics {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin: 0.7rem 0 0.15rem 0;
}
.ci-report-chip {
    display: inline-flex;
    align-items: baseline;
    gap: 0.3rem;
    border: 1px solid var(--ci-border);
    border-radius: 999px;
    padding: 0.22rem 0.6rem;
    background: #fafafa;
    font-size: 0.78rem;
    color: var(--ci-muted);
}
.ci-report-chip strong {
    color: var(--ci-text);
    font-weight: 650;
    font-size: 0.84rem;
}
.ci-report-card {
    background: var(--ci-card);
    border: 1px solid var(--ci-border);
    border-radius: 12px;
    box-shadow: var(--ci-shadow);
    padding: 0.85rem 0.9rem 0.25rem 0.9rem;
    margin: 0 0 0.2rem 0;
    min-height: 100%;
}
.ci-report-card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.3rem;
}
.ci-report-card .ci-report-run { font-size: 1.05rem; }
.ci-report-prev-wrap { margin-top: 0.35rem; }
</style>
"""


def _download_button(path: Path | None, *, label: str, key: str) -> None:
    if path is None or not path.is_file():
        return
    mime = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if path.suffix.lower() == ".xlsx"
        else "text/plain"
    )
    st.download_button(
        label=label,
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        key=key,
    )


def _render_downloads(
    report: DiscoveredReport, *, prefix: str, excel_label: str, psv_label: str
) -> None:
    c1, c2 = st.columns(2)
    with c1:
        if report.has_excel:
            _download_button(
                report.excel_path,
                label=excel_label,
                key=f"{prefix}-xlsx-{report.run_id}",
            )
    with c2:
        if report.has_psv:
            _download_button(
                report.psv_path,
                label=psv_label,
                key=f"{prefix}-psv-{report.run_id}",
            )


def _status_pill(report: DiscoveredReport) -> str:
    status = report.status_label()
    if not status:
        return ""
    kind = pill_kind_for_status(status)
    return f'<span class="ci-pill ci-pill-{html.escape(kind)}">{html.escape(status)}</span>'


def _latest_html(report: DiscoveredReport) -> str:
    lines = [html.escape(report.datetime_label())]
    run_type = report.run_type_label()
    if run_type:
        lines.append(html.escape(run_type))
    retailers = report.retailers_label()
    if retailers:
        lines.append(html.escape(retailers))
    chips = []
    for label, value in report.metrics:
        chips.append(
            '<span class="ci-report-chip">'
            f"<strong>{html.escape(str(value))}</strong> {html.escape(label)}"
            "</span>"
        )
    metrics = (
        f'<div class="ci-report-metrics">{"".join(chips)}</div>' if chips else ""
    )
    return f"""
    <div class="ci-report-latest">
      <div class="ci-report-latest-head">
        <div class="ci-report-run">Run {html.escape(str(report.run_id))}</div>
        {_status_pill(report)}
      </div>
      <div class="ci-report-meta">{"<br/>".join(lines)}</div>
      {metrics}
    </div>
    """


def _previous_html(report: DiscoveredReport) -> str:
    meta = [html.escape(report.datetime_label())]
    run_type = report.run_type_label()
    if run_type:
        meta.append(html.escape(run_type))
    return f"""
    <div class="ci-report-card">
      <div class="ci-report-card-head">
        <div class="ci-report-run">Run {html.escape(str(report.run_id))}</div>
        {_status_pill(report)}
      </div>
      <div class="ci-report-meta">{"<br/>".join(meta)}</div>
    </div>
    """


def render() -> None:
    section_header("Reports", "Download collection reports and historical exports.")
    st.markdown(_CSS, unsafe_allow_html=True)
    reports = discover_reports()
    if reports:
        try:
            with read_session() as session:
                reports = attach_run_metadata(session, reports)
        except Exception:  # noqa: BLE001 — file library still works without metadata
            pass
    latest, previous = split_latest_and_previous(reports)
    if latest is None:
        empty_state(
            "No reports available yet",
            "Reports are generated automatically after a production collection.",
        )
        return
    st.markdown(
        '<div class="ci-report-info">'
        "Each report represents one collection run. Excel and PSV contain the same "
        "run-scoped analytics in different formats.<br/>"
        "Historical reports are preserved and are not replaced by newer collections."
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown('<div class="ci-report-kicker">Latest report</div>', unsafe_allow_html=True)
    st.markdown(_latest_html(latest), unsafe_allow_html=True)
    _render_downloads(
        latest, prefix="latest", excel_label="Download Excel", psv_label="Download PSV"
    )
    if not previous:
        return
    st.markdown(
        '<div class="ci-report-kicker" style="margin-top:1.1rem;">Previous reports</div>',
        unsafe_allow_html=True,
    )
    for index in range(0, len(previous), 2):
        pair = previous[index : index + 2]
        columns = st.columns(2)
        for column, report in zip(columns, pair):
            with column:
                st.markdown(_previous_html(report), unsafe_allow_html=True)
                _render_downloads(
                    report, prefix="prev", excel_label="Excel", psv_label="PSV"
                )
