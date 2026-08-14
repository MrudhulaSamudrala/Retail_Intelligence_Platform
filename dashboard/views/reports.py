"""Reports — download existing Excel/PSV exports. Does not regenerate."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard.components.layout import section_header
from dashboard.db import read_session
from dashboard.queries.reports import (
    DiscoveredReport,
    attach_run_metadata,
    discover_reports,
)


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


def _heading(report: DiscoveredReport) -> None:
    st.markdown(f"**Run {report.run_id}**")
    bits = [report.datetime_label()]
    status = report.status_label()
    if status:
        bits.append(status)
    st.caption(" · ".join(bits))
    scope = report.scope_label()
    if scope:
        st.caption(scope)


def _render_downloads(
    report: DiscoveredReport, *, prefix: str, excel_label: str, psv_label: str
) -> None:
    c1, c2 = st.columns(2)
    with c1:
        if report.has_excel:
            _download_button(
                report.excel_path,
                label=excel_label,
                key=f"{prefix}-xlsx-{report.run_id}-{report.date}",
            )
    with c2:
        if report.has_psv:
            _download_button(
                report.psv_path,
                label=psv_label,
                key=f"{prefix}-psv-{report.run_id}-{report.date}",
            )


def render() -> None:
    section_header("Reports", "Download collection reports and historical exports.")
    reports = discover_reports()
    if reports:
        try:
            with read_session() as session:
                reports = attach_run_metadata(session, reports)
        except Exception:  # noqa: BLE001 — file library still works without metadata
            pass
    if not reports:
        st.caption("No generated reports found. Reports are created after a production collection.")
        return
    latest, *previous = reports
    st.markdown("**LATEST REPORT**")
    _heading(latest)
    _render_downloads(
        latest, prefix="latest", excel_label="Download Excel", psv_label="Download PSV"
    )
    if previous:
        st.markdown("**PREVIOUS REPORTS**")
        for report in previous:
            _heading(report)
            _render_downloads(report, prefix="prev", excel_label="Excel", psv_label="PSV")
