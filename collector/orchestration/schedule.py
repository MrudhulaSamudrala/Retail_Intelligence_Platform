"""Host schedule for production collection (one-shot runner, no background loop).

The existing entry point remains ``python -m collector.run --all``.
This module is the in-repo definition of the 3× daily UTC schedule loaded from
``config/retailers.yaml`` → ``scheduling``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
RETAILERS_CONFIG = ROOT / "config" / "retailers.yaml"

PRODUCTION_COMMAND = ("python", "-m", "collector.run", "--all")
DEFAULT_CRON = "0 8,14,20 * * *"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_HOURS = (8, 14, 20)


@dataclass(frozen=True)
class CollectionSchedule:
    timezone_name: str
    cron: str
    hours: tuple[int, ...]
    collections_per_day: int
    command: tuple[str, ...]
    retailers: tuple[str, ...]


def _parse_cron_hours(cron: str) -> tuple[int, ...]:
    """Parse hours from a 5-field cron ``m h * * *`` (minute hour ...)."""
    parts = (cron or "").split()
    if len(parts) < 2:
        return DEFAULT_HOURS
    hour_field = parts[1]
    hours: list[int] = []
    for token in hour_field.split(","):
        token = token.strip()
        if token.isdigit():
            hours.append(int(token))
    return tuple(hours) if hours else DEFAULT_HOURS


@lru_cache(maxsize=1)
def load_collection_schedule() -> CollectionSchedule:
    data: dict[str, Any] = {}
    if RETAILERS_CONFIG.exists():
        with RETAILERS_CONFIG.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    scheduling = data.get("scheduling") or {}
    cron = str(scheduling.get("production_cron") or DEFAULT_CRON).strip()
    tz_name = str(scheduling.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    hours = _parse_cron_hours(cron)
    retailers = tuple(
        str(item["code"])
        for item in (data.get("retailers") or [])
        if isinstance(item, dict) and item.get("enabled", True) and item.get("code")
    )
    return CollectionSchedule(
        timezone_name=tz_name,
        cron=cron,
        hours=hours,
        collections_per_day=len(hours),
        command=PRODUCTION_COMMAND,
        retailers=retailers or ("newegg", "mercadolibre"),
    )


def schedule_zoneinfo(schedule: CollectionSchedule | None = None) -> ZoneInfo:
    cfg = schedule or load_collection_schedule()
    return ZoneInfo(cfg.timezone_name)


def daily_run_times(
    *,
    on_date: datetime | None = None,
    schedule: CollectionSchedule | None = None,
) -> list[datetime]:
    """Return the three scheduled instants for a calendar day in the project TZ."""
    cfg = schedule or load_collection_schedule()
    tz = schedule_zoneinfo(cfg)
    base = on_date.astimezone(tz) if on_date is not None else datetime.now(tz)
    day = base.date()
    return [
        datetime.combine(day, time(hour=hour, minute=0), tzinfo=tz)
        for hour in cfg.hours
    ]


def is_scheduled_hour(
    moment: datetime,
    *,
    schedule: CollectionSchedule | None = None,
) -> bool:
    cfg = schedule or load_collection_schedule()
    tz = schedule_zoneinfo(cfg)
    local = moment.astimezone(tz)
    return local.hour in cfg.hours and local.minute == 0


async def run_scheduled_tick(
    session: Session,
    *,
    dry_run: bool = False,
    product_limit: Optional[int] = None,
    retailers: Optional[Sequence[str]] = None,
):
    """Invoke the existing production runner as a scheduled tick."""
    from collector.orchestration.runner import run_production
    from collector.universe_config import search_universe_size

    return await run_production(
        session,
        product_limit=product_limit if product_limit is not None else search_universe_size(),
        retailers=retailers,
        dry_run=dry_run,
        trigger_source="scheduled",
    )
