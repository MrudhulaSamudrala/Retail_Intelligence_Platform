"""Discover existing generated reports. Does not regenerate files."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import CollectionRun, CollectionRunStep
from reporting.paths import reports_root

_STEM = re.compile(
    r"^BridgeAI_Report_Run_(?P<run_id>\d+)_(?P<date>\d{4}-\d{2}-\d{2})$",
    re.IGNORECASE,
)

_RETAILER_LABELS = {
    "newegg": "Newegg",
    "mercadolibre": "Mercado Libre",
}
_SKIP_METRIC_STATUSES = frozenset(
    {"SKIPPED", "NOT_AVAILABLE", "NOT AVAILABLE", "PENDING", "UNKNOWN", ""}
)
_PRODUCT_COMPONENTS = ("newegg", "mercadolibre")
_METRIC_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("audits", "Audits"),
    ("badges", "Badges"),
    ("pricing", "Price Snapshots"),
    ("banners", "Banners"),
    ("search", "Search Observations"),
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
    retailers: tuple[str, ...] = ()
    metrics: tuple[tuple[str, int], ...] = ()

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
        return format_report_datetime(moment)

    def run_type_label(self) -> str | None:
        return human_run_type(self.run_type)

    def retailers_label(self) -> str | None:
        if not self.retailers:
            return None
        return "Retailers: " + ", ".join(self.retailers)


def format_report_datetime(moment: datetime) -> str:
    dt = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    name = local.tzname() or ""
    offset = local.utcoffset()
    if name in {"India Standard Time", "India Daylight Time"} or (
        offset is not None and offset.total_seconds() == 5.5 * 3600
    ):
        abbrev = "IST"
    else:
        abbrev = name or "local"
    return f"{local.strftime('%d %b %Y')} · {local.strftime('%H:%M')} {abbrev}"


def human_run_type(run_type: str | None) -> str | None:
    raw = (run_type or "").strip()
    if not raw:
        return None
    if raw.lower() == "production":
        return "Production collection"
    label = raw.replace("_", " ").strip().title()
    if "collection" in label.lower():
        return label
    return f"{label} collection"


def human_retailer(code: str) -> str:
    key = (code or "").strip().lower()
    if key in _RETAILER_LABELS:
        return _RETAILER_LABELS[key]
    if key in {"multi", "xx", ""}:
        return ""
    return code.replace("_", " ").strip().title()


def metrics_from_steps(steps: list[CollectionRunStep]) -> tuple[tuple[str, int], ...]:
    """Surface stored step record counts. Does not query observation tables."""
    by_name = {str(step.component).lower(): step for step in steps}
    out: list[tuple[str, int]] = []
    product_total = 0
    product_found = False
    for component in _PRODUCT_COMPONENTS:
        step = by_name.get(component)
        if step is None:
            continue
        if str(step.status or "").upper() in _SKIP_METRIC_STATUSES:
            continue
        if int(step.records_processed or 0) <= 0:
            continue
        product_found = True
        product_total += int(step.records_processed)
    if product_found:
        out.append(("Products", product_total))
    for component, label in _METRIC_COMPONENTS:
        step = by_name.get(component)
        if step is None:
            continue
        if str(step.status or "").upper() in _SKIP_METRIC_STATUSES:
            continue
        count = int(step.records_processed or 0)
        if count <= 0:
            continue
        out.append((label, count))
    return tuple(out)


def retailers_from_run(run: CollectionRun, steps: list[CollectionRunStep]) -> tuple[str, ...]:
    names: list[str] = []
    meta = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    raw = meta.get("retailers")
    if isinstance(raw, list):
        for item in raw:
            label = human_retailer(str(item))
            if label and label not in names:
                names.append(label)
    if not names:
        for step in steps:
            label = human_retailer(str(step.component))
            if label and label not in names:
                names.append(label)
    fallback = human_retailer(run.retailer_code or "")
    if not names and fallback:
        names.append(fallback)
    return tuple(names)


def dedupe_reports_by_run_id(reports: list[DiscoveredReport]) -> list[DiscoveredReport]:
    """One library card per collection_run_id. Newer files win; missing format is filled."""
    by_id: dict[int, DiscoveredReport] = {}
    for item in sorted(reports, key=lambda report: report.sort_key):
        current = by_id.get(item.run_id)
        if current is None:
            by_id[item.run_id] = item
            continue
        newer, older = (
            (item, current) if item.sort_key >= current.sort_key else (current, item)
        )
        by_id[item.run_id] = replace(
            newer,
            excel_path=newer.excel_path or older.excel_path,
            psv_path=newer.psv_path or older.psv_path,
        )
    return sorted(by_id.values(), key=lambda report: report.sort_key, reverse=True)


def split_latest_and_previous(
    reports: list[DiscoveredReport],
) -> tuple[DiscoveredReport | None, list[DiscoveredReport]]:
    unique = dedupe_reports_by_run_id(reports)
    if not unique:
        return None, []
    return unique[0], unique[1:]


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
    return dedupe_reports_by_run_id(reports)


def attach_run_metadata(
    session: Session, reports: list[DiscoveredReport]
) -> list[DiscoveredReport]:
    """Fill collection_runs fields. Does not scan the filesystem or generate files."""
    if not reports:
        return []
    run_ids = {item.run_id for item in reports}
    rows = session.scalars(select(CollectionRun).where(CollectionRun.id.in_(run_ids))).all()
    by_id = {int(run.id): run for run in rows}
    steps = list(
        session.scalars(
            select(CollectionRunStep).where(CollectionRunStep.collection_run_id.in_(run_ids))
        ).all()
    )
    steps_by_run: dict[int, list[CollectionRunStep]] = {}
    for step in steps:
        steps_by_run.setdefault(int(step.collection_run_id), []).append(step)
    enriched: list[DiscoveredReport] = []
    for item in reports:
        run = by_id.get(item.run_id)
        if run is None:
            enriched.append(item)
            continue
        run_steps = steps_by_run.get(item.run_id, [])
        enriched.append(
            replace(
                item,
                run_type=run.run_type,
                status=run.status,
                started_at=run.started_at,
                completed_at=run.completed_at,
                retailer_code=run.retailer_code,
                country_code=run.country_code,
                retailers=retailers_from_run(run, run_steps),
                metrics=metrics_from_steps(run_steps),
                display_date=(
                    (run.completed_at or run.started_at).strftime("%d %b %Y")
                    if (run.completed_at or run.started_at)
                    else item.display_date
                ),
            )
        )
    return dedupe_reports_by_run_id(enriched)


def latest_report(*, root: Path | None = None) -> DiscoveredReport | None:
    items = discover_reports(root=root)
    return items[0] if items else None
