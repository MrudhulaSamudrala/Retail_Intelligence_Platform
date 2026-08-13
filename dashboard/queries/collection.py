"""Thin query helpers unique to the dashboard (status / filter options).

Prefer analytics modules for metrics. These helpers only support UI needs
that analytics does not already expose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from dashboard.config import dashboard_meta
from database.models import CollectionRun, CollectionRunStep, Product


@dataclass
class ComponentStatus:
    component: str
    status: str  # SUCCESS | PARTIAL | FAILED | SKIPPED | UNKNOWN
    completed_at: Optional[datetime] = None
    records_processed: int = 0
    error_message: Optional[str] = None
    details: Optional[dict[str, Any]] = None

    @property
    def reason(self) -> str:
        if self.error_message:
            return self.error_message
        if isinstance(self.details, dict):
            for key in ("reason", "block_reason", "message", "access_status"):
                if self.details.get(key):
                    return str(self.details[key])
        return ""


@dataclass
class CollectionStatusSnapshot:
    latest_run_id: Optional[int] = None
    latest_status: str = "NO_DATA"
    latest_started_at: Optional[datetime] = None
    latest_completed_at: Optional[datetime] = None
    last_successful_at: Optional[datetime] = None
    retailers: list[str] = field(default_factory=list)
    is_partial: bool = False
    is_stale: bool = False
    is_live: bool = False
    freshness_label: str = "No collection data"
    components: list[ComponentStatus] = field(default_factory=list)
    next_scheduled_hint: str = "3× daily (08:00 / 14:00 / 20:00 UTC)"
    frequency: str = "3 collections per day"


def filter_option_values(session: Session) -> dict[str, list[str]]:
    """Distinct English-normalized dimension values from ``products``."""
    def _col(column) -> list[str]:
        rows = session.scalars(
            select(distinct(column))
            .where(column.is_not(None))
            .where(Product.is_active.is_(True))
            .order_by(column)
        ).all()
        return [str(r) for r in rows if r]

    return {
        "retailer_code": _col(Product.retailer_code),
        "country_code": _col(Product.country_code),
        "product_type": _col(Product.product_type),
        "brand": _col(Product.brand),
        "oem": _col(Product.oem),
    }


def count_tracked_products(session: Session, *, retailer_code: Optional[str] = None) -> int:
    stmt = select(func.count()).select_from(Product).where(Product.is_active.is_(True))
    if retailer_code:
        stmt = stmt.where(Product.retailer_code == retailer_code)
    return int(session.scalar(stmt) or 0)


def load_collection_status(session: Session) -> CollectionStatusSnapshot:
    meta = dashboard_meta()
    stale_hours = float(meta.get("stale_hours", 12))
    frequency = f"{meta.get('collections_per_day', 3)} collections per day"
    cron = str(meta.get("schedule_cron", "0 8,14,20 * * *"))

    latest = session.scalars(
        select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(1)
    ).first()

    last_ok = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.status.in_(("completed", "SUCCESS", "success", "partial", "PARTIAL")))
        .order_by(CollectionRun.completed_at.desc().nullslast(), CollectionRun.started_at.desc())
        .limit(1)
    ).first()

    snap = CollectionStatusSnapshot(
        frequency=frequency,
        next_scheduled_hint=f"Cron `{cron}` ({meta.get('schedule_timezone', 'UTC')})",
    )

    if latest is None:
        snap.freshness_label = "No collection runs in database"
        return snap

    status = (latest.status or "").upper()
    snap.latest_run_id = latest.id
    snap.latest_status = status
    snap.latest_started_at = latest.started_at
    snap.latest_completed_at = latest.completed_at
    snap.is_partial = status in {"PARTIAL", "PARTIAL_SUCCESS"}
    snap.retailers = sorted(
        {
            r
            for r in session.scalars(
                select(distinct(CollectionRun.retailer_code))
                .where(CollectionRun.id == latest.id)
            ).all()
            if r
        }
        or ([latest.retailer_code] if latest.retailer_code else [])
    )

    # Orchestration runs may use retailer_code='all' — collect from steps/metadata
    if latest.retailer_code and latest.retailer_code.lower() != "all":
        snap.retailers = sorted(set(snap.retailers) | {latest.retailer_code})

    if last_ok is not None:
        snap.last_successful_at = last_ok.completed_at or last_ok.started_at

    now = datetime.now(timezone.utc)
    ref = snap.last_successful_at or snap.latest_completed_at or snap.latest_started_at
    if ref is not None:
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        age = now - ref
        snap.is_stale = age > timedelta(hours=stale_hours)
    else:
        snap.is_stale = True

    snap.is_live = (
        status in {"COMPLETED", "SUCCESS"}
        and not snap.is_stale
        and not snap.is_partial
    )

    if snap.is_partial:
        snap.freshness_label = "PARTIAL DATA"
    elif snap.is_stale:
        snap.freshness_label = "Stale — latest successful collection is older than threshold"
    elif snap.is_live:
        snap.freshness_label = "Live / Latest successful collection"
    elif status in {"FAILED", "ERROR"}:
        snap.freshness_label = "Latest collection FAILED"
    else:
        snap.freshness_label = f"Latest run status: {status or 'UNKNOWN'}"

    steps = session.scalars(
        select(CollectionRunStep)
        .where(CollectionRunStep.collection_run_id == latest.id)
        .order_by(CollectionRunStep.component.asc())
    ).all()
    for step in steps:
        snap.components.append(
            ComponentStatus(
                component=step.component,
                status=(step.status or "UNKNOWN").upper(),
                completed_at=step.completed_at,
                records_processed=int(step.records_processed or 0),
                error_message=step.error_message,
                details=step.details if isinstance(step.details, dict) else None,
            )
        )

    return snap


def recent_runs(session: Session, *, limit: int = 10) -> list[CollectionRun]:
    return list(
        session.scalars(
            select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(limit)
        ).all()
    )
