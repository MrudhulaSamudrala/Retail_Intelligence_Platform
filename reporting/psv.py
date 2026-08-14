"""Pipe-separated value export for the main report table (brand compliance)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from analytics.compliance.config import CHECK_CODES

MAIN_COLUMNS = ("brand",) + tuple(c.lower() for c in CHECK_CODES) + ("overall",)


def _escape(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("|", "\\|")


def write_psv(
    path: Path,
    rows: Iterable[dict[str, Any]],
    *,
    columns: tuple[str, ...] = MAIN_COLUMNS,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["|".join(columns)]
    for row in rows:
        lines.append("|".join(_escape(row.get(col, "")) for col in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
