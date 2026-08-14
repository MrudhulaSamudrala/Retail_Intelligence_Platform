"""Excel workbook writer for a collection report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

SHEETS: tuple[tuple[str, str], ...] = (
    ("Executive Summary", "executive"),
    ("Shelf Presence", "shelf"),
    ("Search Visibility", "visibility"),
    ("Pricing", "pricing"),
    ("Promotions", "promotions"),
    ("Brand Compliance", "compliance"),
    ("Banner Tracking", "banners"),
    ("Product Data Quality", "quality"),
    ("Badge Coverage", "badges"),
    ("Product Data", "products"),
)


def _write_sheet(ws, rows: list[dict[str, Any]]) -> None:
    if not rows:
        ws.append(["No data available for this collection"])
        return
    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    header_font = Font(bold=True)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = header_font
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    for idx, header in enumerate(headers, start=1):
        width = min(max(len(str(header)) + 2, 12), 48)
        ws.column_dimensions[get_column_letter(idx)].width = width


def write_excel(path: Path, tables: dict[str, list[dict[str, Any]]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    first = True
    for title, key in SHEETS:
        if first:
            ws = workbook.active
            ws.title = title
            first = False
        else:
            ws = workbook.create_sheet(title)
        _write_sheet(ws, tables.get(key) or [])
    workbook.save(path)
    return path
