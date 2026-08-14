"""Generate Excel + PSV reports from existing PostgreSQL data.

Does not collect products, does not update observation rows, and does not
invent metric values. Collection status is owned by the orchestrator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import CollectionRun, CollectionRunStep
from reporting.excel import write_excel
from reporting.paths import ReportPaths, paths_for_run
from reporting.psv import write_psv
from reporting.tables import build_report_tables

logger = logging.getLogger("reporting.generate")


@dataclass
class ReportGenerationResult:
    run_id: int
    status: str
    excel_path: Optional[Path] = None
    psv_path: Optional[Path] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "SUCCESS"


def _observed_at(run: CollectionRun) -> datetime:
    moment = run.completed_at or run.started_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def generate_reports_for_run(
    session: Session,
    run_id: int,
    *,
    reports_root: Path | None = None,
) -> ReportGenerationResult:
    logger.info(
        "report_generation_started",
        extra={"event": "report_generation_started", "run_id": run_id},
    )
    run = session.get(CollectionRun, run_id)
    if run is None:
        msg = f"collection_run_id={run_id} not found"
        logger.error(
            "report_generation_failed",
            extra={"event": "report_generation_failed", "run_id": run_id, "error": msg},
        )
        return ReportGenerationResult(run_id=run_id, status="FAILED", error_message=msg)

    steps = list(
        session.scalars(
            select(CollectionRunStep)
            .where(CollectionRunStep.collection_run_id == run_id)
            .order_by(CollectionRunStep.component.asc())
        ).all()
    )
    try:
        tables = build_report_tables(session, run=run, steps=steps)
        paths = paths_for_run(run.id, _observed_at(run), root=reports_root)
        excel_path = write_excel(paths.excel, tables)
        logger.info(
            "excel_report_generated",
            extra={
                "event": "excel_report_generated",
                "run_id": run_id,
                "path": str(excel_path),
            },
        )
        psv_path = write_psv(paths.psv, tables.get("compliance") or [])
        logger.info(
            "psv_report_generated",
            extra={
                "event": "psv_report_generated",
                "run_id": run_id,
                "path": str(psv_path),
            },
        )
        return ReportGenerationResult(
            run_id=run_id,
            status="SUCCESS",
            excel_path=excel_path,
            psv_path=psv_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "report_generation_failed",
            extra={
                "event": "report_generation_failed",
                "run_id": run_id,
                "error": str(exc),
            },
        )
        return ReportGenerationResult(
            run_id=run_id,
            status="FAILED",
            error_message=f"{type(exc).__name__}: {exc}",
        )


def generate_reports_for_latest(
    session: Session, *, reports_root: Path | None = None
) -> ReportGenerationResult:
    run = session.scalars(
        select(CollectionRun)
        .where(CollectionRun.run_type == "production")
        .order_by(CollectionRun.started_at.desc())
        .limit(1)
    ).first()
    if run is None:
        run = session.scalars(
            select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(1)
        ).first()
    if run is None:
        return ReportGenerationResult(
            run_id=0, status="FAILED", error_message="no_collection_runs"
        )
    return generate_reports_for_run(session, run.id, reports_root=reports_root)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from dotenv import load_dotenv

    from database.connection import get_engine, get_session_factory

    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate BridgeAI reports from existing DB data")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args(argv)
    engine = get_engine()
    session = get_session_factory(engine)()
    try:
        if args.run_id is not None:
            result = generate_reports_for_run(session, args.run_id)
        else:
            result = generate_reports_for_latest(session)
        print(
            {
                "status": result.status,
                "run_id": result.run_id,
                "excel": str(result.excel_path) if result.excel_path else None,
                "psv": str(result.psv_path) if result.psv_path else None,
                "error": result.error_message,
            }
        )
        return 0 if result.ok else 1
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
