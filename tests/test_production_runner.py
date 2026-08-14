"""Unit tests for production orchestration (mocked collectors — no live sites)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.orchestration.config import (
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    OrchestrationConfig,
)
from collector.orchestration.lock import has_active_production_run, production_lock
from collector.orchestration.runner import ProductionRunner, _aggregate_status
from collector.orchestration.steps import StepResult, with_retries
from database.models import Base, CollectionRun, CollectionRunStep, PriceHistory, Product


def _fast_config() -> OrchestrationConfig:
    return OrchestrationConfig(
        product_limit_per_retailer=5,
        search_limit_per_retailer=1,
        stale_running_hours=6,
        concurrent_lock_key=1,
        exit_code_partial=0,
        max_attempts=2,
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
        completed_at=datetime.now(timezone.utc),
    )


def _fail(component: str, msg: str = "boom") -> StepResult:
    return StepResult(
        component=component,
        status=STATUS_FAILED,
        records_processed=0,
        error_message=msg,
        completed_at=datetime.now(timezone.utc),
    )


def test_aggregate_status_success_partial_failed() -> None:
    assert _aggregate_status([STATUS_SUCCESS, STATUS_SUCCESS]) == STATUS_SUCCESS
    assert _aggregate_status([STATUS_SUCCESS, STATUS_FAILED]) == STATUS_PARTIAL
    assert _aggregate_status([STATUS_FAILED, STATUS_FAILED]) == STATUS_FAILED
    assert _aggregate_status([STATUS_SUCCESS, STATUS_PARTIAL]) == STATUS_PARTIAL
    assert _aggregate_status([STATUS_SKIPPED, STATUS_SUCCESS]) == STATUS_SUCCESS


def test_with_retries_succeeds_after_transient_failure() -> None:
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("temp")
        return "ok"

    result = asyncio.run(
        with_retries(flaky, max_attempts=3, base_delay_seconds=0.01, label="test")
    )
    assert result == "ok"
    assert calls["n"] == 3


def test_with_retries_exhausts() -> None:
    async def always_fail():
        raise TimeoutError("nav")

    with pytest.raises(TimeoutError):
        asyncio.run(
            with_retries(
                always_fail, max_attempts=2, base_delay_seconds=0.01, label="t"
            )
        )


def test_concurrent_run_prevention(session: Session) -> None:
    session.add(
        CollectionRun(
            retailer_code="multi",
            country_code="XX",
            run_type="production",
            status=STATUS_RUNNING,
            started_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    with production_lock(session, lock_key=1, stale_hours=6) as acquired:
        assert acquired is False
    assert has_active_production_run(session) is not None


def test_successful_complete_run(session: Session) -> None:
    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), product_limit=5, search_limit=1
        )
        with (
            patch(
                "collector.orchestration.runner.run_newegg_products",
                new=AsyncMock(return_value=_ok("newegg", 21)),
            ),
            patch(
                "collector.orchestration.runner.run_mercadolibre_products",
                new=AsyncMock(return_value=_ok("mercadolibre", 18)),
            ),
            patch(
                "collector.orchestration.runner.run_audits_step",
                new=AsyncMock(return_value=_ok("audits", 39)),
            ),
            patch(
                "collector.orchestration.runner.run_badges_step",
                new=AsyncMock(return_value=_ok("badges", 39)),
            ),
            patch(
                "collector.orchestration.runner.run_pricing_step",
                new=AsyncMock(return_value=_ok("pricing", 39)),
            ),
            patch(
                "collector.orchestration.runner.run_banners_step",
                new=AsyncMock(return_value=_ok("banners", 2)),
            ),
            patch(
                "collector.orchestration.runner.run_search_step",
                new=AsyncMock(return_value=_ok("search", 6)),
            ),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_SUCCESS
    assert result.run_id is not None
    assert result.exit_code == 0
    run = session.get(CollectionRun, result.run_id)
    assert run is not None
    assert run.status == STATUS_SUCCESS
    assert run.run_type == "production"
    assert run.completed_at is not None
    steps = session.scalars(
        select(CollectionRunStep).where(
            CollectionRunStep.collection_run_id == result.run_id
        )
    ).all()
    assert len(steps) == 7
    assert {s.status for s in steps} == {STATUS_SUCCESS}


def test_partial_retailer_failure(session: Session) -> None:
    async def _run():
        runner = ProductionRunner(session, config=_fast_config())
        with (
            patch(
                "collector.orchestration.runner.run_newegg_products",
                new=AsyncMock(return_value=_ok("newegg", 10)),
            ),
            patch(
                "collector.orchestration.runner.run_mercadolibre_products",
                new=AsyncMock(return_value=_fail("mercadolibre", "adapter missing")),
            ),
            patch(
                "collector.orchestration.runner.run_audits_step",
                new=AsyncMock(return_value=_ok("audits", 10)),
            ),
            patch(
                "collector.orchestration.runner.run_badges_step",
                new=AsyncMock(return_value=_ok("badges", 10)),
            ),
            patch(
                "collector.orchestration.runner.run_pricing_step",
                new=AsyncMock(return_value=_ok("pricing", 10)),
            ),
            patch(
                "collector.orchestration.runner.run_banners_step",
                new=AsyncMock(return_value=_ok("banners", 2)),
            ),
            patch(
                "collector.orchestration.runner.run_search_step",
                new=AsyncMock(return_value=_ok("search", 3)),
            ),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_PARTIAL
    by = {s.component: s.status for s in result.steps}
    assert by["newegg"] == STATUS_SUCCESS
    assert by["mercadolibre"] == STATUS_FAILED
    run = session.get(CollectionRun, result.run_id)
    assert run.status == STATUS_PARTIAL


def test_complete_failure(session: Session) -> None:
    async def _run():
        runner = ProductionRunner(session, config=_fast_config())
        fail = AsyncMock(side_effect=RuntimeError("down"))
        with (
            patch("collector.orchestration.runner.run_newegg_products", new=fail),
            patch(
                "collector.orchestration.runner.run_mercadolibre_products", new=fail
            ),
            patch("collector.orchestration.runner.run_audits_step", new=fail),
            patch("collector.orchestration.runner.run_badges_step", new=fail),
            patch("collector.orchestration.runner.run_pricing_step", new=fail),
            patch("collector.orchestration.runner.run_banners_step", new=fail),
            patch("collector.orchestration.runner.run_search_step", new=fail),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_FAILED
    assert result.exit_code == 1
    assert all(s.status == STATUS_FAILED for s in result.steps)


def test_run_status_transitions_and_steps(session: Session) -> None:
    seen_running = {"v": False}

    async def newegg_side(*_a, **_k):
        run = session.scalars(
            select(CollectionRun).where(CollectionRun.run_type == "production")
        ).first()
        assert run is not None
        assert run.status == STATUS_RUNNING
        seen_running["v"] = True
        step = session.scalars(
            select(CollectionRunStep).where(CollectionRunStep.component == "newegg")
        ).first()
        assert step is not None
        assert step.status == STATUS_RUNNING
        return _ok("newegg", 1)

    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), steps=["newegg"]
        )
        with patch(
            "collector.orchestration.runner.run_newegg_products",
            new=AsyncMock(side_effect=newegg_side),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert seen_running["v"]
    assert result.status == STATUS_SUCCESS
    run = session.get(CollectionRun, result.run_id)
    assert run.status == STATUS_SUCCESS


def test_historical_preservation_and_product_upsert(session: Session) -> None:
    """Repeated observations append; product master stays unique."""
    session.add(
        Product(
            retailer_code="newegg",
            country_code="US",
            retailer_sku="SKU-1",
            title="Laptop",
            canonical_url="https://www.newegg.com/p/SKU-1",
        )
    )
    session.commit()
    before = session.scalar(select(func.count()).select_from(Product))
    assert before == 1

    product = session.scalars(select(Product)).first()
    assert product is not None
    for i in range(2):
        session.add(
            PriceHistory(
                product_id=product.id,
                observed_at=datetime.now(timezone.utc),
                price_amount=80000 - i * 2000,
                currency="USD",
            )
        )
    session.commit()
    prices = session.scalar(select(func.count()).select_from(PriceHistory))
    assert prices == 2
    after = session.scalar(select(func.count()).select_from(Product))
    assert after == 1


def test_duplicate_protection_second_run_new_parent(session: Session) -> None:
    async def once():
        runner = ProductionRunner(
            session, config=_fast_config(), steps=["pricing"]
        )
        with patch(
            "collector.orchestration.runner.run_pricing_step",
            new=AsyncMock(return_value=_ok("pricing", 5)),
        ):
            return await runner.run()

    r1 = asyncio.run(once())
    r2 = asyncio.run(once())
    assert r1.run_id != r2.run_id
    runs = session.scalars(
        select(CollectionRun).where(CollectionRun.run_type == "production")
    ).all()
    assert len(runs) == 2


def test_dry_run_no_inserts(session: Session) -> None:
    runner = ProductionRunner(session, dry_run=True)
    with patch.object(
        ProductionRunner,
        "validate_environment",
        return_value={"ok": True, "checks": {"database": "ok"}},
    ):
        result = asyncio.run(runner.run())
    assert result.dry_run is True
    assert result.run_id is None
    assert session.scalar(select(func.count()).select_from(CollectionRun)) == 0


def test_cli_all_ignores_partial_step_list() -> None:
    from collector.run import parse_args, resolve_orchestration_filters

    args = parse_args(
        [
            "--all",
            "--step",
            "newegg",
            "--step",
            "mercadolibre",
            "--step",
            "audits",
            "--step",
            "badges",
            "--step",
            "pricing",
        ]
    )
    retailers, steps = resolve_orchestration_filters(args)
    assert args.all is True
    assert steps is None
    assert retailers is None
    banners_only = parse_args(["--step", "banners"])
    _, only_steps = resolve_orchestration_filters(banners_only)
    assert only_steps == ["banners"]
    pricing_only = parse_args(["--step", "pricing"])
    _, pricing_steps = resolve_orchestration_filters(pricing_only)
    assert pricing_steps == ["pricing"]


def test_cli_parse_all() -> None:
    from collector.run import STEP_CHOICES, parse_args

    args = parse_args(["--all"])
    assert args.all is True
    args2 = parse_args(["--step", "audits", "--step", "banners"])
    assert args2.step == ["audits", "banners"]
    args3 = parse_args(["--all", "--dry-run"])
    assert args3.dry_run is True
    assert "banners" in STEP_CHOICES
    banners_only = parse_args(["--step", "banners"])
    assert banners_only.step == ["banners"]
    assert banners_only.all is False
    assert banners_only.retailer is None


def test_cli_requires_mode() -> None:
    from collector.run import parse_args

    with pytest.raises(SystemExit):
        parse_args([])


def test_clean_shutdown_finalizes_failed_on_component_exception(
    session: Session,
) -> None:
    async def _run():
        runner = ProductionRunner(
            session, config=_fast_config(), steps=["newegg"]
        )
        with patch(
            "collector.orchestration.runner.run_newegg_products",
            new=AsyncMock(side_effect=RuntimeError("browser died")),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_FAILED
    run = session.get(CollectionRun, result.run_id)
    assert run is not None
    assert run.status == STATUS_FAILED
    assert run.completed_at is not None
    step = session.scalars(
        select(CollectionRunStep).where(
            CollectionRunStep.collection_run_id == result.run_id
        )
    ).first()
    assert step is not None
    assert step.status == STATUS_FAILED
    assert "browser died" in (step.error_message or "")


def test_configuration_validation_keys(session: Session) -> None:
    runner = ProductionRunner(session, dry_run=True)
    validation = runner.validate_environment()
    assert "checks" in validation
    assert "database" in validation["checks"]
    assert "tables" in validation["checks"]


def test_banners_step_is_discoverable_and_skipped_when_omitted(session: Session) -> None:
    from collector.orchestration.config import COMPONENTS

    assert "banners" in COMPONENTS
    runner = ProductionRunner(
        session,
        config=_fast_config(),
        steps=["newegg", "mercadolibre", "audits", "badges", "pricing"],
    )
    assert runner._component_enabled("banners") is False
    assert runner._component_enabled("search") is False
    assert runner._component_enabled("newegg") is True


def test_banners_step_enabled_for_step_flag_and_all(session: Session) -> None:
    banners_only = ProductionRunner(session, config=_fast_config(), steps=["banners"])
    assert banners_only._component_enabled("banners") is True
    assert banners_only._component_enabled("newegg") is False
    full = ProductionRunner(session, config=_fast_config())
    assert full._component_enabled("banners") is True
    narrowed_all = ProductionRunner(
        session, config=_fast_config(), retailers=["newegg"]
    )
    assert narrowed_all._component_enabled("banners") is True
    assert narrowed_all._component_enabled("newegg") is True
    assert narrowed_all._component_enabled("mercadolibre") is False


def test_banners_only_run_calls_existing_collector(session: Session) -> None:
    banners = AsyncMock(return_value=_ok("banners", 3))
    product = AsyncMock(return_value=_ok("newegg", 10))

    async def _run():
        runner = ProductionRunner(session, config=_fast_config(), steps=["banners"])
        with (
            patch("collector.orchestration.runner.run_banners_step", new=banners),
            patch("collector.orchestration.runner.run_newegg_products", new=product),
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
                "collector.orchestration.runner.run_search_step",
                new=AsyncMock(return_value=_ok("search")),
            ),
        ):
            return await runner.run()

    result = asyncio.run(_run())
    assert result.status == STATUS_SUCCESS
    banners.assert_awaited_once()
    product.assert_not_called()
    by = {s.component: s for s in result.steps}
    assert by["banners"].status == STATUS_SUCCESS
    assert by["newegg"].status == STATUS_SKIPPED
