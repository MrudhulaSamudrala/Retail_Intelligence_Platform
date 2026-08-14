"""Reports — download existing Excel/PSV exports. Does not regenerate."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard.components.layout import section_header
from dashboard.queries.reports import DiscoveredReport, discover_reports, latest_report


def _download_button(path: Path | None, *, label: str, key: str) -> None:
    if path is None or not path.is_file():
        st.caption(f"{label} unavailable")
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


def _render_item(report: DiscoveredReport, *, prefix: str, excel_label: str, psv_label: str) -> None:
    st.markdown(f"**Run {report.run_id}**")
    st.caption(report.display_date)
    c1, c2 = st.columns(2)
    with c1:
        _download_button(report.excel_path, label=excel_label, key=f"{prefix}-xlsx-{report.run_id}-{report.date}")
    with c2:
        _download_button(report.psv_path, label=psv_label, key=f"{prefix}-psv-{report.run_id}-{report.date}")


def render() -> None:
    section_header("Reports", "Download collection reports and historical exports.")
    reports = discover_reports()
    if not reports:
        st.caption("No generated reports found. Reports are created after a production collection.")
        return
    latest = latest_report()
    previous = reports[1:] if latest else reports
    if latest:
        st.markdown("**Latest report**")
        st.caption(f"{latest.display_date} · Run {latest.run_id}")
        c1, c2 = st.columns(2)
        with c1:
            _download_button(
                latest.excel_path,
                label="Download Excel",
                key=f"latest-xlsx-{latest.run_id}-{latest.date}",
            )
        with c2:
            _download_button(
                latest.psv_path,
                label="Download PSV",
                key=f"latest-psv-{latest.run_id}-{latest.date}",
            )
    if previous:
        st.markdown("**Previous Reports**")
        for report in previous:
            _render_item(report, prefix="prev", excel_label="Excel", psv_label="PSV")
