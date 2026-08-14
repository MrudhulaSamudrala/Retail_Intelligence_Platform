"""Report generation and discovery tests — no live collection."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.orchestration.config import STATUS_SUCCESS, OrchestrationConfig
from collector.orchestration.runner import ProductionRunner
from collector.orchestration.steps import StepResult
from dashboard.queries.reports import discover_reports, latest_report
from dashboard.views.collection_status import _component_status, _normalize
from database.models import Base, CollectionRun
from reporting.excel import write_excel
from reporting.generate import generate_reports_for_run
from reporting.paths import paths_for_run
from reporting.psv import write_psv


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def test_psv_and_excel_writers(tmp_path: Path) -> None:
    rows = [
        {
            "brand": "Intel",
            "s1": "94",
            "s2": "94",
            "p1": "100",
            "p2": "94",
            "p3": "93",
            "p4": "94",
            "p5": "89",
            "overall": "99",
        },
        {
            "brand": "AMD",
            "s1": "88",
            "s2": "88",
            "p1": "94",
            "p2": "88",
            "p3": "65",
            "p4": "88",
            "p5": "82",
            "overall": "100",
        },
    ]
    psv = write_psv(tmp_path / "out.psv", rows)
    text = psv.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "brand|s1|s2|p1|p2|p3|p4|p5|overall"
    assert "Intel|94|94|100|94|93|94|89|99" in text
    excel = write_excel(
        tmp_path / "out.xlsx",
        {"executive": [{"field": "status", "value": "SUCCESS"}], "compliance": rows},
    )
    assert excel.is_file()
    assert excel.stat().st_size > 0


def test_report_discovery_groups_latest(tmp_path: Path) -> None:
    day = tmp_path / "2026-08-14"
    day.mkdir()
    (day / "BridgeAI_Report_Run_8_2026-08-14.xlsx").write_bytes(b"xlsx")
    (day / "BridgeAI_Report_Run_8_2026-08-14.psv").write_text("brand|overall\n", encoding="utf-8")
    (day / "BridgeAI_Report_Run_18_2026-08-14.xlsx").write_bytes(b"xlsx")
    (day / "BridgeAI_Report_Run_18_2026-08-14.psv").write_text("brand|overall\n", encoding="utf-8")
    found = discover_reports(root=tmp_path)
    assert [item.run_id for item in found] == [18, 8]
    latest = latest_report(root=tmp_path)
    assert latest is not None
    assert latest.run_id == 18
    assert latest.has_excel and latest.has_psv
    assert "Aug 2026" in latest.display_date


def test_paths_use_run_id_and_date() -> None:
    paths = paths_for_run(18, datetime(2026, 8, 14, 16, 7, tzinfo=timezone.utc))
    assert paths.stem == "BridgeAI_Report_Run_18_2026-08-14"
    assert paths.excel.name.endswith(".xlsx")
    assert paths.psv.name.endswith(".psv")


def test_generate_reports_does_not_mutate_run(session: Session, tmp_path: Path) -> None:
    run = CollectionRun(
        retailer_code="multi",
        country_code="XX",
        run_type="production",
        status=STATUS_SUCCESS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.commit()
    generate_reports_for_run(session, run.id, reports_root=tmp_path)
    session.refresh(run)
    assert run.status == STATUS_SUCCESS


def test_collection_status_does_not_invent_success() -> None:
    from dashboard.queries.collection import ComponentStatus

    assert _normalize(None) == "NOT AVAILABLE"
    assert _normalize("SUCCESS") == "SUCCESS"
    assert _normalize("completed") == "SUCCESS"
    assert _normalize("PARTIAL") == "PARTIAL"
    by = {
        "audits": ComponentStatus(component="audits", status="SUCCESS"),
        "newegg": ComponentStatus(component="newegg", status="PARTIAL"),
        "mercadolibre": ComponentStatus(component="mercadolibre", status="SUCCESS"),
    }
    assert _component_status("banners", by) == "NOT AVAILABLE"
    assert _component_status("audits", by) == "SUCCESS"
    assert _component_status("products", by) == "PARTIAL"


def test_report_failure_does_not_fail_collection(session: Session) -> None:
    def _ok(component: str) -> StepResult:
        return StepResult(
            component=component,
            status=STATUS_SUCCESS,
            records_processed=1,
            completed_at=datetime.now(timezone.utc),
        )

    async def _run():
        runner = ProductionRunner(
            session,
            config=OrchestrationConfig(
                product_limit_per_retailer=1,
                search_limit_per_retailer=1,
                stale_running_hours=6,
                concurrent_lock_key=1,
                exit_code_partial=0,
                max_attempts=1,
                base_delay_seconds=0.01,
                component_timeout_seconds=30,
                page_timeout_ms=1000,
            ),
        )
        with (
            patch(
                "collector.orchestration.runner.run_newegg_products",
                new=AsyncMock(return_value=_ok("newegg")),
            ),
            patch(
                "collector.orchestration.runner.run_mercadolibre_products",
                new=AsyncMock(return_value=_ok("mercadolibre")),
            ),
            patch(
                "collector.orchestration.runner.run_audits_step",
                new=AsyncMock(return_value=_ok("audits")),
            ),
            patch(
                "collector.orchestration.runner.run_badges_step",
                new=AsyncMock(return_value=_ok("badges")),
            ),
            patch(
                "collector.orchestration.runner.run_pricing_step",
                new=AsyncMock(return_value=_ok("pricing")),
            ),
            patch(
                "collector.orchestration.runner.run_banners_step",
                new=AsyncMock(return_value=_ok("banners")),
            ),
            patch(
                "collector.orchestration.runner.run_search_step",
                new=AsyncMock(return_value=_ok("search")),
            ),
            patch(
                "collector.orchestration.runner._attempt_reports",
                side_effect=RuntimeError("excel exploded"),
            ),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_SUCCESS
    run = session.get(CollectionRun, result.run_id)
    assert run is not None
    assert run.status == STATUS_SUCCESS
    assert result.report_status == "FAILED"


def test_scheduler_scripts_use_venv_python() -> None:
    launcher = Path("scripts/run_scheduled_collection.ps1").read_text(encoding="utf-8")
    setup = Path("scripts/setup_windows_scheduler.ps1").read_text(encoding="utf-8")
    assert ".venv\\Scripts\\python.exe" in launcher
    assert "collector.run" in launcher
    assert "--all" in launcher
    assert "streamlit run" not in launcher.lower()
    assert "BridgeAI - Production Collection" in setup or "task_name" in setup
    assert "run_scheduled_collection.ps1" in setup
    assert "streamlit run" not in setup.lower()
    assert "config/schedule.yaml" in Path("docs/windows_scheduler.md").read_text(
        encoding="utf-8"
    )
