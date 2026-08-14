"""Host schedule for production collection (one-shot runner, no background loop).

The existing entry point remains ``python -m collector.run --all``.
Times live in ``config/schedule.yaml`` so Windows Task Scheduler, the launcher,
and the dashboard share one configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_CONFIG = ROOT / "config" / "schedule.yaml"
RETAILERS_CONFIG = ROOT / "config" / "retailers.yaml"

PRODUCTION_COMMAND = ("python", "-m", "collector.run", "--all")
DEFAULT_CRON = "0 8,14,20 * * *"
DEFAULT_HOURS = (8, 14, 20)
DEFAULT_MINUTE = 0
DEFAULT_TASK_NAME = "BridgeAI - Production Collection"


@dataclass(frozen=True)
class CollectionSchedule:
    timezone_name: str
    cron: str
    hours: tuple[int, ...]
    minute: int
    collections_per_day: int
    command: tuple[str, ...]
    retailers: tuple[str, ...]
    task_name: str
    log_dir: str


@dataclass(frozen=True)
class NextScheduled:
    when: datetime
    is_tomorrow: bool
    clock_label: str
    tz_abbrev: str
    display_label: str


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


def _local_timezone_name() -> str:
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    if key:
        return str(key)
    name = tz.tzname(datetime.now()) if tz is not None else None
    return str(name or "local")


def _hours_to_cron(hours: Sequence[int], minute: int) -> str:
    hour_field = ",".join(str(h) for h in hours)
    return f"{int(minute)} {hour_field} * * *"


@lru_cache(maxsize=1)
def load_collection_schedule() -> CollectionSchedule:
    schedule_data: dict[str, Any] = {}
    if SCHEDULE_CONFIG.exists():
        with SCHEDULE_CONFIG.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        schedule_data = raw.get("schedule") or raw or {}

    retailers_data: dict[str, Any] = {}
    if RETAILERS_CONFIG.exists():
        with RETAILERS_CONFIG.open(encoding="utf-8") as handle:
            retailers_data = yaml.safe_load(handle) or {}
    scheduling = retailers_data.get("scheduling") or {}

    hours_raw = schedule_data.get("hours")
    if hours_raw:
        hours = tuple(int(h) for h in hours_raw)
    else:
        cron_fallback = str(scheduling.get("production_cron") or DEFAULT_CRON).strip()
        hours = _parse_cron_hours(cron_fallback)
    minute = int(schedule_data.get("minute") if schedule_data.get("minute") is not None else DEFAULT_MINUTE)

    tz_name = str(
        schedule_data.get("timezone")
        or scheduling.get("timezone")
        or "local"
    ).strip() or "local"

    retailers = tuple(
        str(item["code"])
        for item in (retailers_data.get("retailers") or [])
        if isinstance(item, dict) and item.get("enabled", True) and item.get("code")
    )
    log_dir = str(schedule_data.get("log_dir") or "logs/collections")
    task_name = str(schedule_data.get("task_name") or DEFAULT_TASK_NAME)
    return CollectionSchedule(
        timezone_name=tz_name,
        cron=_hours_to_cron(hours, minute),
        hours=hours or DEFAULT_HOURS,
        minute=minute,
        collections_per_day=len(hours or DEFAULT_HOURS),
        command=PRODUCTION_COMMAND,
        retailers=retailers or ("newegg", "mercadolibre"),
        task_name=task_name,
        log_dir=log_dir,
    )


def invalidate_schedule_cache() -> None:
    load_collection_schedule.cache_clear()


def schedule_tzinfo(schedule: CollectionSchedule | None = None):
    cfg = schedule or load_collection_schedule()
    name = (cfg.timezone_name or "local").strip()
    if name.lower() in {"local", "system", "machine"}:
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().tzinfo or timezone.utc


def schedule_zoneinfo(schedule: CollectionSchedule | None = None):
    """Back-compat alias used by existing tests."""
    return schedule_tzinfo(schedule)


def _tz_abbrev(moment: datetime) -> str:
    name = moment.tzname() or ""
    if name in {"India Standard Time", "India Daylight Time"}:
        return "IST"
    if len(name) <= 5 and name.isalpha():
        return name
    offset = moment.utcoffset()
    if offset == timedelta(hours=5, minutes=30):
        return "IST"
    if offset == timedelta(0):
        return "UTC"
    return name or "local"


def daily_run_times(
    *,
    on_date: datetime | None = None,
    schedule: CollectionSchedule | None = None,
) -> list[datetime]:
    """Return scheduled instants for a calendar day in the configured TZ."""
    cfg = schedule or load_collection_schedule()
    tz = schedule_tzinfo(cfg)
    base = on_date.astimezone(tz) if on_date is not None else datetime.now(tz)
    day = base.date()
    return [
        datetime.combine(day, time(hour=hour, minute=cfg.minute), tzinfo=tz)
        for hour in cfg.hours
    ]


def is_scheduled_hour(
    moment: datetime,
    *,
    schedule: CollectionSchedule | None = None,
) -> bool:
    cfg = schedule or load_collection_schedule()
    tz = schedule_tzinfo(cfg)
    local = moment.astimezone(tz)
    return local.hour in cfg.hours and local.minute == cfg.minute


def next_scheduled_collection(
    now: datetime | None = None,
    *,
    schedule: CollectionSchedule | None = None,
) -> NextScheduled:
    cfg = schedule or load_collection_schedule()
    tz = schedule_tzinfo(cfg)
    current = (now or datetime.now(tz)).astimezone(tz)
    for hour in cfg.hours:
        slot = datetime.combine(
            current.date(), time(hour=hour, minute=cfg.minute), tzinfo=tz
        )
        if current < slot:
            abbrev = _tz_abbrev(slot)
            clock = slot.strftime("%H:%M")
            return NextScheduled(
                when=slot,
                is_tomorrow=False,
                clock_label=clock,
                tz_abbrev=abbrev,
                display_label=f"{clock} {abbrev}",
            )
    tomorrow = current.date() + timedelta(days=1)
    first_hour = cfg.hours[0] if cfg.hours else 8
    slot = datetime.combine(
        tomorrow, time(hour=first_hour, minute=cfg.minute), tzinfo=tz
    )
    abbrev = _tz_abbrev(slot)
    clock = slot.strftime("%H:%M")
    return NextScheduled(
        when=slot,
        is_tomorrow=True,
        clock_label=clock,
        tz_abbrev=abbrev,
        display_label=f"{clock} {abbrev} · Tomorrow",
    )


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
