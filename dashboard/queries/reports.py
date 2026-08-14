"""Discover existing generated reports. Does not regenerate files."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import CollectionRun
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
    run_type: str | None = None
    status: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retailer_code: str | None = None
    country_code: str | None = None

    @property
    def has_excel(self) -> bool:
        return self.excel_path is not None and self.excel_path.is_file()

    @property
    def has_psv(self) -> bool:
        return self.psv_path is not None and self.psv_path.is_file()

    @property
    def sort_key(self) -> tuple:
        moment = self.completed_at or self.started_at or self.timestamp
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (moment, self.run_id)

    def status_label(self) -> str | None:
        if not self.status:
            return None
        return str(self.status).upper()

    def datetime_label(self) -> str:
        moment = self.completed_at or self.started_at
        if moment is None:
            return self.display_date
        return moment.strftime("%d %b %Y · %H:%M")

    def scope_label(self) -> str | None:
        parts = [p for p in (self.retailer_code, self.country_code, self.run_type) if p]
        return " · ".join(parts) if parts else None


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
    reports.sort(key=lambda item: item.sort_key, reverse=True)
    return reports


def attach_run_metadata(
    session: Session, reports: list[DiscoveredReport]
) -> list[DiscoveredReport]:
    """Fill collection_runs fields. Does not scan the filesystem or generate files."""
    if not reports:
        return []
    run_ids = {item.run_id for item in reports}
    rows = session.scalars(select(CollectionRun).where(CollectionRun.id.in_(run_ids))).all()
    by_id = {int(run.id): run for run in rows}
    enriched: list[DiscoveredReport] = []
    for item in reports:
        run = by_id.get(item.run_id)
        if run is None:
            enriched.append(item)
            continue
        enriched.append(
            replace(
                item,
                run_type=run.run_type,
                status=run.status,
                started_at=run.started_at,
                completed_at=run.completed_at,
                retailer_code=run.retailer_code,
                country_code=run.country_code,
                display_date=(
                    (run.completed_at or run.started_at).strftime("%d %b %Y")
                    if (run.completed_at or run.started_at)
                    else item.display_date
                ),
            )
        )
    enriched.sort(key=lambda item: item.sort_key, reverse=True)
    return enriched


def latest_report(*, root: Path | None = None) -> DiscoveredReport | None:
    items = discover_reports(root=root)
    return items[0] if items else None
