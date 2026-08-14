"""Multi-section pipe-separated export. Same tables as Excel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reporting.sections import REPORT_SECTIONS, unavailable_rows

SEPARATOR = "=" * 50


def _escape(value: Any) -> str:
    if value is None:
        return "N/A"
    text = str(value)
    if text == "":
        return "N/A"
    return text.replace("|", "\\|")


def _headers(rows: list[dict[str, Any]]) -> list[str]:
    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    return headers


def write_psv(path: Path, tables: dict[str, list[dict[str, Any]]]) -> Path:
    """Write every report section from the shared analytics tables."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for heading, key, _sheet, _chart in REPORT_SECTIONS:
        rows = list(tables.get(key) or [])
        if not rows:
            rows = unavailable_rows()
        headers = _headers(rows)
        lines.append(SEPARATOR)
        lines.append(heading)
        lines.append(SEPARATOR)
        lines.append("")
        lines.append("|".join(headers))
        for row in rows:
            lines.append("|".join(_escape(row.get(col)) for col in headers))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
