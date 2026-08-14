"""Scheduler / automation layer tests — mocked collectors, no live sites."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.base import CollectionOutcome
from collector.orchestration.config import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    OrchestrationConfig,
    load_orchestration_config,
)
from collector.orchestration.runner import ProductionRunner, run_production
from collector.orchestration.schedule import (
    PRODUCTION_COMMAND,
    daily_run_times,
    is_scheduled_hour,
    load_collection_schedule,
    run_scheduled_tick,
    schedule_zoneinfo,
)
from collector.orchestration.steps import StepResult, product_collection_step_status
from collector.universe_config import STRATUM_BUDGETS, search_universe_size
from database.models import Base, CollectionRun, PriceHistory, Product


def _fast_config() -> OrchestrationConfig:
    return OrchestrationConfig(
        product_limit_per_retailer=5,
        search_limit_per_retailer=1,
        stale_running_hours=6,
        concurrent_lock_key=1,
        exit_code_partial=0,
        max_attempts=1,
        base_delay_seconds=0.01,
        component_timeout_seconds=30,
        page_timeout_ms=1000,
    )


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


def _ok(component: str, records: int = 1) -> StepResult:
    return StepResult(
        component=component,
        status=STATUS_SUCCESS,
        records_processed=records,
        completed_at=datetime.now(tz=ZoneInfo("UTC")),
    )


def _status(component: str, status: str, msg: str | None = None) -> StepResult:
    return StepResult(
        component=component,
        status=status,
        records_processed=0,
        error_message=msg,
        details={"bot_blocked": status == STATUS_PARTIAL},
        completed_at=datetime.now(tz=ZoneInfo("UTC")),
    )


def _patch_pipeline(**overrides):
    defaults = {
        "collector.orchestration.runner.run_newegg_products": AsyncMock(
            return_value=_ok("newegg", 100)
        ),
        "collector.orchestration.runner.run_mercadolibre_products": AsyncMock(
            return_value=_ok("mercadolibre", 100)
        ),
        "collector.orchestration.runner.run_audits_step": AsyncMock(
            return_value=_ok("audits", 1)
        ),
        "collector.orchestration.runner.run_badges_step": AsyncMock(
            return_value=_ok("badges", 1)
        ),
        "collector.orchestration.runner.run_pricing_step": AsyncMock(
            return_value=_ok("pricing", 1)
        ),
        "collector.orchestration.runner.run_banners_step": AsyncMock(
            return_value=_ok("banners", 1)
        ),
        "collector.orchestration.runner.run_search_step": AsyncMock(
            return_value=_ok("search", 1)
        ),
    }
    defaults.update(overrides)
    return defaults


def test_three_daily_slots_and_timezone() -> None:
    from collector.orchestration.schedule import invalidate_schedule_cache, next_scheduled_collection

    invalidate_schedule_cache()
    cfg = load_collection_schedule()
    assert cfg.timezone_name.lower() == "local"
    assert cfg.hours == (8, 14, 20)
    assert cfg.minute == 0
    assert cfg.collections_per_day == 3
    assert cfg.task_name == "BridgeAI - Production Collection"
    tz = schedule_zoneinfo(cfg)
    day = datetime(2026, 8, 14, tzinfo=tz)
    times = daily_run_times(on_date=day, schedule=cfg)
    assert [t.hour for t in times] == [8, 14, 20]
    assert is_scheduled_hour(datetime(2026, 8, 14, 8, 0, tzinfo=tz), schedule=cfg)
    assert is_scheduled_hour(datetime(2026, 8, 14, 14, 0, tzinfo=tz), schedule=cfg)
    assert is_scheduled_hour(datetime(2026, 8, 14, 20, 0, tzinfo=tz), schedule=cfg)
    assert not is_scheduled_hour(
        datetime(2026, 8, 14, 8, 1, tzinfo=tz), schedule=cfg
    )
    nxt = next_scheduled_collection(
        datetime(2026, 8, 14, 16, 0, tzinfo=tz), schedule=cfg
    )
    assert nxt.is_tomorrow is False
    assert nxt.clock_label == "20:00"
    later = next_scheduled_collection(
        datetime(2026, 8, 14, 21, 0, tzinfo=tz), schedule=cfg
    )
    assert later.is_tomorrow is True
    assert later.clock_label == "08:00"
    assert "Tomorrow" in later.display_label


def test_production_entry_point_and_both_retailers() -> None:
    cfg = load_collection_schedule()
    assert cfg.command == PRODUCTION_COMMAND
    assert cfg.command == ("python", "-m", "collector.run", "--all")
    assert "newegg" in cfg.retailers
    assert "mercadolibre" in cfg.retailers
    source = inspect.getsource(run_scheduled_tick)
    assert "run_production" in source
    assert "CollectionPipeline" not in source


def test_universe_size_and_stratum_budgets_from_config() -> None:
    assert search_universe_size() == 100
    assert STRATUM_BUDGETS == {
        "notebook": 20,
        "desktop": 20,
        "workstation": 20,
        "tablet": 20,
        "gpu": 10,
        "cpu": 10,
    }
    orch = load_orchestration_config()
    assert orch.product_limit_per_retailer == search_universe_size()


def test_scheduled_tick_invokes_existing_runner_with_config_limit() -> None:
    session = object()

    async def _run():
        with patch(
            "collector.orchestration.runner.run_production",
            new=AsyncMock(return_value="ok"),
        ) as mocked:
            result = await run_scheduled_tick(session, dry_run=True)  # type: ignore[arg-type]
            mocked.assert_called_once()
            kwargs = mocked.call_args.kwargs
            assert kwargs["trigger_source"] == "scheduled"
            assert kwargs["product_limit"] == 100
            assert kwargs["dry_run"] is True
            assert result == "ok"

    asyncio.run(_run())


def test_concurrent_scheduled_run_skipped(session: Session) -> None:
    session.add(
        CollectionRun(
            retailer_code="multi",
            country_code="XX",
            run_type="production",
            status=STATUS_RUNNING,
            started_at=datetime.now(tz=ZoneInfo("UTC")),
        )
    )
    session.commit()
    runner = ProductionRunner(
        session, config=_fast_config(), trigger_source="scheduled"
    )
    result = asyncio.run(runner.run())
    assert result.skipped_concurrent is True
    assert result.status == STATUS_SKIPPED
    assert result.run_id is None
    assert session.scalar(select(func.count()).select_from(CollectionRun)) == 1


def test_failed_newegg_still_attempts_mercadolibre(session: Session) -> None:
    newegg = AsyncMock(return_value=_status("newegg", STATUS_FAILED, "timeout"))
    ml = AsyncMock(return_value=_ok("mercadolibre", 50))
    patches = _patch_pipeline(
        **{
            "collector.orchestration.runner.run_newegg_products": newegg,
            "collector.orchestration.runner.run_mercadolibre_products": ml,
        }
    )

    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), trigger_source="scheduled"
        )
        with (
            patch("collector.orchestration.runner.run_newegg_products", new=newegg),
            patch("collector.orchestration.runner.run_mercadolibre_products", new=ml),
            patch("collector.orchestration.runner.run_audits_step", new=patches["collector.orchestration.runner.run_audits_step"]),
            patch("collector.orchestration.runner.run_badges_step", new=patches["collector.orchestration.runner.run_badges_step"]),
            patch("collector.orchestration.runner.run_pricing_step", new=patches["collector.orchestration.runner.run_pricing_step"]),
            patch("collector.orchestration.runner.run_banners_step", new=patches["collector.orchestration.runner.run_banners_step"]),
            patch("collector.orchestration.runner.run_search_step", new=patches["collector.orchestration.runner.run_search_step"]),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert newegg.await_count == 1
    assert ml.await_count == 1
    by = {s.component: s.status for s in result.steps}
    assert by["newegg"] == STATUS_FAILED
    assert by["mercadolibre"] == STATUS_SUCCESS
    assert result.status == STATUS_PARTIAL


def test_failed_ml_still_attempts_newegg(session: Session) -> None:
    newegg = AsyncMock(return_value=_ok("newegg", 50))
    ml = AsyncMock(return_value=_status("mercadolibre", STATUS_FAILED, "verification"))

    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), trigger_source="scheduled"
        )
        with (
            patch("collector.orchestration.runner.run_newegg_products", new=newegg),
            patch("collector.orchestration.runner.run_mercadolibre_products", new=ml),
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
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert newegg.await_count == 1
    assert ml.await_count == 1
    by = {s.component: s.status for s in result.steps}
    assert by["newegg"] == STATUS_SUCCESS
    assert by["mercadolibre"] == STATUS_FAILED
    assert result.status == STATUS_PARTIAL


def test_blocked_retailer_is_not_complete() -> None:
    blocked = CollectionOutcome(
        status="completed",
        bot_blocked=True,
        universe={"completeness": "BLOCKED", "search_status": "BLOCKED"},
        requested=100,
        observed=0,
    )
    assert product_collection_step_status(blocked) == STATUS_PARTIAL
    complete = CollectionOutcome(
        status="completed",
        bot_blocked=False,
        universe={"completeness": "COMPLETE"},
        requested=100,
        observed=100,
    )
    assert product_collection_step_status(complete) == STATUS_SUCCESS
    failed = CollectionOutcome(status="failed", universe={"completeness": "FAILED"})
    assert product_collection_step_status(failed) == STATUS_FAILED


def test_blocked_step_does_not_make_overall_success(session: Session) -> None:
    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), trigger_source="scheduled"
        )
        with (
            patch(
                "collector.orchestration.runner.run_newegg_products",
                new=AsyncMock(return_value=_ok("newegg", 100)),
            ),
            patch(
                "collector.orchestration.runner.run_mercadolibre_products",
                new=AsyncMock(
                    return_value=_status(
                        "mercadolibre", STATUS_PARTIAL, "account verification"
                    )
                ),
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
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_PARTIAL
    run = session.get(CollectionRun, result.run_id)
    assert run is not None
    assert run.status == STATUS_PARTIAL
    by = {s.component: s.status for s in result.steps}
    assert by["newegg"] == STATUS_SUCCESS
    assert by["mercadolibre"] == STATUS_PARTIAL


def test_scheduled_run_metadata(session: Session) -> None:
    async def _run():
        runner = ProductionRunner(
            session,
            config=_fast_config(),
            trigger_source="scheduled",
            steps=["pricing"],
        )
        with patch(
            "collector.orchestration.runner.run_pricing_step",
            new=AsyncMock(return_value=_ok("pricing", 3)),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    run = session.get(CollectionRun, result.run_id)
    assert run is not None
    meta = run.run_metadata or {}
    assert meta["source"] == "scheduled"
    assert meta["trigger"] == "scheduled"
    assert meta["search_universe_size"] == 100
    assert meta["stratum_budgets"] == STRATUM_BUDGETS
    assert run.started_at is not None
    assert run.completed_at is not None
    assert run.retailer_code == "multi"


def test_manual_cli_still_works() -> None:
    from collector.run import parse_args

    manual = parse_args(["--all"])
    assert manual.all is True
    assert manual.scheduled is False
    scheduled = parse_args(["--scheduled"])
    assert scheduled.all is True
    assert scheduled.scheduled is True
    dry = parse_args(["--all", "--dry-run"])
    assert dry.dry_run is True
    assert dry.scheduled is False


def test_manual_run_metadata_is_cli(session: Session) -> None:
    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), steps=["pricing"], trigger_source="cli"
        )
        with patch(
            "collector.orchestration.runner.run_pricing_step",
            new=AsyncMock(return_value=_ok("pricing", 1)),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    run = session.get(CollectionRun, result.run_id)
    assert (run.run_metadata or {})["source"] == "cli"


def test_no_second_pipeline_in_schedule_module() -> None:
    import collector.orchestration.schedule as sched

    text = inspect.getsource(sched)
    assert "APScheduler" not in text
    assert "BackgroundScheduler" not in text
    assert "run_production" in text


def test_scheduled_runs_are_append_only(session: Session) -> None:
    session.add(
        Product(
            retailer_code="newegg",
            country_code="US",
            retailer_sku="SKU-KEEP",
            title="Keep me",
            canonical_url="https://www.newegg.com/p/SKU-KEEP",
        )
    )
    session.commit()
    product = session.scalars(select(Product)).first()
    assert product is not None
    session.add(
        PriceHistory(
            product_id=product.id,
            observed_at=datetime.now(tz=ZoneInfo("UTC")),
            price_amount=10000,
            currency="USD",
        )
    )
    session.commit()

    async def once():
        runner = ProductionRunner(
            session, config=_fast_config(), trigger_source="scheduled", steps=["pricing"]
        )
        with patch(
            "collector.orchestration.runner.run_pricing_step",
            new=AsyncMock(return_value=_ok("pricing", 1)),
        ):
            return await runner.run()

    asyncio.run(once())
    asyncio.run(once())
    assert session.scalar(select(func.count()).select_from(Product)) == 1
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 1
    runs = session.scalars(
        select(CollectionRun).where(CollectionRun.run_type == "production")
    ).all()
    assert len(runs) == 2


def test_no_live_collection_in_scheduler_module() -> None:
    import collector.orchestration.schedule as sched

    src = inspect.getsource(sched)
    assert "playwright" not in src
    assert "build_collector" not in src
    assert "newegg.com" not in src
    assert "mercadolivre.com" not in src
