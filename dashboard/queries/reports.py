"""Discover existing generated reports. Does not regenerate files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from reporting.paths import reports_root

_STEM = re.compile(
    r"^BridgeAI_Report_Run_(?P<run_id>\d+)_(?P<date>\d{4}-\d{2}-\d{2})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscoveredReport:
    run_id: int
    date: str
    excel_path: Path | None
    psv_path: Path | None
    timestamp: datetime
    display_date: str

    @property
    def has_excel(self) -> bool:
        return self.excel_path is not None and self.excel_path.is_file()

    @property
    def has_psv(self) -> bool:
        return self.psv_path is not None and self.psv_path.is_file()


def _mtime(path: Path | None) -> float:
    if path is None or not path.is_file():
        return 0.0
    return path.stat().st_mtime


def _display_date(iso_date: str) -> str:
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date
    return dt.strftime("%d %b %Y")


def discover_reports(*, root: Path | None = None) -> list[DiscoveredReport]:
    base = root or reports_root()
    if not base.exists():
        return []
    grouped: dict[tuple[int, str], dict[str, Path]] = {}
    for path in base.rglob("BridgeAI_Report_Run_*"):
        if not path.is_file():
            continue
        match = _STEM.match(path.stem)
        if not match:
            continue
        run_id = int(match.group("run_id"))
        date = match.group("date")
        key = (run_id, date)
        bucket = grouped.setdefault(key, {})
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            bucket["excel"] = path
        elif suffix == ".psv":
            bucket["psv"] = path
    reports: list[DiscoveredReport] = []
    for (run_id, date), files in grouped.items():
        excel = files.get("excel")
        psv = files.get("psv")
        stamp = max(_mtime(excel), _mtime(psv))
        reports.append(
            DiscoveredReport(
                run_id=run_id,
                date=date,
                excel_path=excel,
                psv_path=psv,
                timestamp=datetime.fromtimestamp(stamp) if stamp else datetime.fromisoformat(date),
                display_date=_display_date(date),
            )
        )
    reports.sort(key=lambda item: (item.date, item.run_id, item.timestamp), reverse=True)
    return reports


def latest_report(*, root: Path | None = None) -> DiscoveredReport | None:
    items = discover_reports(root=root)
    return items[0] if items else None
