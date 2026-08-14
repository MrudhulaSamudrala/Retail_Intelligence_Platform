"""Generate Excel + PSV reports from existing PostgreSQL data.

Does not collect products, does not update observation rows, and does not
invent metric values. Collection status is owned by the orchestrator.
Report generation is read-only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import CollectionRun, CollectionRunStep
from reporting.excel import write_excel
from reporting.paths import paths_for_run
from reporting.psv import write_psv
from reporting.run_scope import list_production_run_ids, parse_run_id_list
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
        psv_path = write_psv(paths.psv, tables)
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
        return ReportGenerationResult(
            run_id=0, status="FAILED", error_message="no_production_collection_runs"
        )
    return generate_reports_for_run(session, run.id, reports_root=reports_root)


def generate_reports_for_runs(
    session: Session,
    run_ids: Sequence[int],
    *,
    reports_root: Path | None = None,
) -> list[ReportGenerationResult]:
    return [
        generate_reports_for_run(session, run_id, reports_root=reports_root)
        for run_id in run_ids
    ]


def generate_historical_production_reports(
    session: Session, *, reports_root: Path | None = None
) -> list[ReportGenerationResult]:
    return generate_reports_for_runs(
        session, list_production_run_ids(session), reports_root=reports_root
    )


def _print_result(result: ReportGenerationResult) -> None:
    print(
        {
            "status": result.status,
            "run_id": result.run_id,
            "excel": str(result.excel_path) if result.excel_path else None,
            "psv": str(result.psv_path) if result.psv_path else None,
            "error": result.error_message,
        }
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    from dotenv import load_dotenv

    from database.connection import get_engine, get_session_factory

    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate BridgeAI reports from existing DB data")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument(
        "--runs",
        type=str,
        default=None,
        help="Comma-separated collection_run_id list, e.g. 8,18",
    )
    parser.add_argument(
        "--historical",
        action="store_true",
        help="Generate reports for every production collection_run (not child component runs).",
    )
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args(argv)
    engine = get_engine()
    session = get_session_factory(engine)()
    try:
        results: list[ReportGenerationResult]
        if args.historical:
            results = generate_historical_production_reports(session)
            if not results:
                results = [
                    ReportGenerationResult(
                        run_id=0,
                        status="FAILED",
                        error_message="no_production_collection_runs",
                    )
                ]
        else:
            run_ids: list[int] = []
            if args.run_id is not None:
                run_ids.append(args.run_id)
            if args.runs:
                run_ids.extend(parse_run_id_list(args.runs))
            # Preserve order, drop duplicates.
            unique: list[int] = []
            seen: set[int] = set()
            for rid in run_ids:
                if rid not in seen:
                    seen.add(rid)
                    unique.append(rid)
            if unique:
                results = generate_reports_for_runs(session, unique)
            else:
                results = [generate_reports_for_latest(session)]
        for result in results:
            _print_result(result)
        return 0 if results and all(item.ok for item in results) else 1
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
