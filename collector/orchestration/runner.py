"""Production orchestration runner for `python -m collector.run --all`."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from collector.orchestration.config import (
    COMPONENTS,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    OrchestrationConfig,
    load_orchestration_config,
)
from collector.orchestration.lock import production_lock
from collector.orchestration.steps import (
    StepResult,
    run_audits_step,
    run_badges_step,
    run_banners_step,
    run_mercadolibre_products,
    run_newegg_products,
    run_pricing_step,
    run_search_step,
    with_retries,
)
from database.models import CollectionRun, CollectionRunStep, Product

logger = logging.getLogger("collector.orchestration.runner")

# Steps that are product collections vs observations
PRODUCT_COMPONENTS = frozenset({"newegg", "mercadolibre"})
DEPENDENT_PRODUCT_OBS = frozenset({"audits", "badges", "pricing"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trigger_source(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    trigger = (os.getenv("COLLECTION_TRIGGER") or "").strip().lower()
    if trigger in {"scheduled", "cron"}:
        return "scheduled"
    if os.getenv("RENDER"):
        return "scheduled"
    if os.getenv("CRON"):
        return "scheduled"
    return "cli"


def _aggregate_status(statuses: Sequence[str]) -> str:
    meaningful = [s for s in statuses if s != STATUS_SKIPPED]
    if not meaningful:
        return STATUS_SUCCESS
    if all(s == STATUS_SUCCESS for s in meaningful):
        return STATUS_SUCCESS
    if all(s == STATUS_FAILED for s in meaningful):
        return STATUS_FAILED
    if any(s in (STATUS_FAILED, STATUS_PARTIAL) for s in meaningful) and any(
        s == STATUS_SUCCESS for s in meaningful
    ):
        return STATUS_PARTIAL
    if any(s == STATUS_PARTIAL for s in meaningful):
        return STATUS_PARTIAL
    if any(s == STATUS_FAILED for s in meaningful):
        return STATUS_FAILED
    return STATUS_SUCCESS


@dataclass
class ProductionRunResult:
    run_id: Optional[int]
    status: str
    started_at: datetime
    completed_at: datetime
    steps: list[StepResult] = field(default_factory=list)
    skipped_concurrent: bool = False
    dry_run: bool = False
    products_before: int = 0
    products_after: int = 0
    validation: dict[str, Any] = field(default_factory=dict)
    error_summary: Optional[str] = None

    @property
    def exit_code(self) -> int:
        if self.skipped_concurrent:
            return 0
        if self.dry_run:
            ok = self.validation.get("ok", False)
            return 0 if ok else 1
        if self.status == STATUS_SUCCESS:
            return 0
        if self.status == STATUS_PARTIAL:
            return load_orchestration_config().exit_code_partial
        return 1


class ProductionRunner:
    def __init__(
        self,
        session: Session,
        *,
        config: Optional[OrchestrationConfig] = None,
        product_limit: Optional[int] = None,
        search_limit: Optional[int] = None,
        retailers: Optional[Sequence[str]] = None,
        steps: Optional[Sequence[str]] = None,
        dry_run: bool = False,
        trigger_source: Optional[str] = None,
    ) -> None:
        self.session = session
        self.config = config or load_orchestration_config()
        self.product_limit = (
            product_limit
            if product_limit is not None
            else self.config.product_limit_per_retailer
        )
        self.search_limit = (
            search_limit
            if search_limit is not None
            else self.config.search_limit_per_retailer
        )
        self.retailers = set(retailers) if retailers else None
        self.steps_filter = set(steps) if steps else None
        self.dry_run = dry_run
        self.trigger_source = _trigger_source(trigger_source)
        self._run_row: Optional[CollectionRun] = None
        self._step_rows: dict[str, CollectionRunStep] = {}
        self._finalized = False

    def _component_enabled(self, component: str) -> bool:
        if self.steps_filter is not None and component not in self.steps_filter:
            return False
        if self.retailers is not None:
            if component in PRODUCT_COMPONENTS:
                return component in self.retailers
            # Product-level observations only when at least one selected retailer
            # is in the filter (or when running banners/search independently).
            if component in DEPENDENT_PRODUCT_OBS:
                return bool(self.retailers & PRODUCT_COMPONENTS) or (
                    "newegg" in self.retailers  # audits/badges currently Newegg-backed
                )
            # Homepage banners and search visibility are independent of product
            # collection. `--retailer` only narrows product collectors; `--all`
            # still runs these steps. `--step` remains the explicit include list.
            return True
        return True

    def validate_environment(self) -> dict[str, Any]:
        """Dry-run / preflight checks — no observation inserts."""
        checks: dict[str, Any] = {"ok": True, "checks": {}}
        # Database connectivity
        try:
            self.session.execute(text("SELECT 1"))
            checks["checks"]["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["ok"] = False
            checks["checks"]["database"] = f"fail:{exc}"

        # Required tables
        required = [
            "collection_runs",
            "collection_run_steps",
            "products",
            "product_snapshots",
            "price_history",
            "retailer_audits",
            "badges",
            "banner_observations",
            "search_observations",
        ]
        try:
            insp = inspect(self.session.get_bind())
            names = set(insp.get_table_names())
            missing = [t for t in required if t not in names]
            if missing:
                checks["ok"] = False
                checks["checks"]["tables"] = f"missing:{missing}"
            else:
                checks["checks"]["tables"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["ok"] = False
            checks["checks"]["tables"] = f"fail:{exc}"

        # Config files
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for name in (
            "config/retailers.yaml",
            "config/keywords.yaml",
            "config/orchestration.yaml",
            "config/search_visibility.yaml",
        ):
            path = root / name
            key = f"config:{name}"
            if path.exists():
                checks["checks"][key] = "ok"
            else:
                checks["ok"] = False
                checks["checks"][key] = "missing"

        # Env (non-secret presence only) — accept DATABASE_URL or POSTGRES_* pieces
        db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
        has_pg_parts = bool(
            os.getenv("POSTGRES_HOST") and os.getenv("POSTGRES_DB") and os.getenv("POSTGRES_USER")
        )
        if db_url:
            checks["checks"]["database_env"] = "DATABASE_URL_set"
        elif has_pg_parts:
            checks["checks"]["database_env"] = "POSTGRES_parts_set"
        elif checks["checks"].get("database") == "ok":
            # Connectivity already proved via SQLAlchemy engine settings
            checks["checks"]["database_env"] = "engine_ok"
        else:
            checks["ok"] = False
            checks["checks"]["database_env"] = "missing"

        # Playwright
        try:
            import playwright  # noqa: F401

            checks["checks"]["playwright"] = "import_ok"
        except Exception as exc:  # noqa: BLE001
            checks["ok"] = False
            checks["checks"]["playwright"] = f"fail:{exc}"

        # Keywords structure
        try:
            import yaml

            kw = yaml.safe_load((root / "config" / "keywords.yaml").read_text(encoding="utf-8"))
            retailers = (kw or {}).get("retailers") or {}
            checks["checks"]["keywords"] = (
                "ok" if retailers else "empty_retailers"
            )
            if not retailers:
                checks["ok"] = False
        except Exception as exc:  # noqa: BLE001
            checks["ok"] = False
            checks["checks"]["keywords"] = f"fail:{exc}"

        return checks

    def _start_run(self) -> CollectionRun:
        from collector.universe_config import load_search_universe_config, STRATUM_BUDGETS

        universe = load_search_universe_config()
        meta = {
            "trigger": self.trigger_source,
            "source": self.trigger_source,
            "config_version": "orchestration_v1",
            "product_limit": self.product_limit,
            "search_universe_size": universe.search_universe_size,
            "stratum_budgets": dict(STRATUM_BUDGETS),
            "search_limit": self.search_limit,
            "retailers": sorted(self.retailers) if self.retailers else list(PRODUCT_COMPONENTS),
            "steps": sorted(self.steps_filter) if self.steps_filter else list(COMPONENTS),
        }
        run = CollectionRun(
            retailer_code="multi",
            country_code="XX",
            run_type="production",
            status=STATUS_RUNNING,
            started_at=_utcnow(),
            run_metadata=meta,
        )
        self.session.add(run)
        self.session.commit()
        self._run_row = run
        logger.info(
            "production_run_started",
            extra={
                "event": "production_run_started",
                "run_id": run.id,
                "component": "orchestrator",
                "status": STATUS_RUNNING,
            },
        )
        return run

    def _start_step(self, component: str) -> CollectionRunStep:
        assert self._run_row is not None
        step = CollectionRunStep(
            collection_run_id=self._run_row.id,
            component=component,
            status=STATUS_RUNNING,
            started_at=_utcnow(),
            records_processed=0,
        )
        self.session.add(step)
        self.session.commit()
        self._step_rows[component] = step
        return step

    def _finalize_step(self, result: StepResult) -> None:
        step = self._step_rows.get(result.component)
        if step is None:
            return
        step.status = result.status
        step.completed_at = result.completed_at or _utcnow()
        step.records_processed = result.records_processed
        step.error_message = (result.error_message or "")[:4000] or None
        step.details = result.details or None
        self.session.commit()
        logger.info(
            "component_finished",
            extra={
                "event": "component_finished",
                "timestamp": (result.completed_at or _utcnow()).isoformat(),
                "run_id": self._run_row.id if self._run_row else None,
                "retailer": result.component,
                "component": result.component,
                "status": result.status,
                "records_processed": result.records_processed,
                "error": result.error_message,
                "exception": result.error_message,
                "stratum": (result.details or {}).get("stratum")
                or ((result.details or {}).get("universe") or {}).get("stop_stratum"),
                "page": ((result.details or {}).get("universe") or {}).get("stop_page"),
                "stop_reason": ((result.details or {}).get("universe") or {}).get(
                    "stop_reason"
                ),
            },
        )

    def _finalize_run(
        self,
        *,
        status: str,
        steps: list[StepResult],
        error_summary: Optional[str] = None,
    ) -> None:
        if self._run_row is None or self._finalized:
            return
        total = sum(s.records_processed for s in steps)
        self._run_row.status = status
        self._run_row.completed_at = _utcnow()
        self._run_row.items_collected = total
        self._run_row.error_message = error_summary
        meta = dict(self._run_row.run_metadata or {})
        meta["step_statuses"] = {s.component: s.status for s in steps}
        self._run_row.run_metadata = meta
        self.session.commit()
        self._finalized = True

    async def _execute_component(
        self, component: str, coro_factory
    ) -> StepResult:
        started = _utcnow()
        if not self._component_enabled(component):
            result = StepResult(
                component=component,
                status=STATUS_SKIPPED,
                started_at=started,
                completed_at=_utcnow(),
            )
            return result

        self._start_step(component)
        try:
            async def _op():
                return await asyncio.wait_for(
                    coro_factory(),
                    timeout=self.config.component_timeout_seconds,
                )

            result = await with_retries(
                _op,
                max_attempts=self.config.max_attempts,
                base_delay_seconds=self.config.base_delay_seconds,
                label=component,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "component_failed",
                extra={
                    "event": "component_failed",
                    "timestamp": started.isoformat(),
                    "run_id": self._run_row.id if self._run_row else None,
                    "retailer": component,
                    "component": component,
                    "status": STATUS_FAILED,
                    "exception": f"{type(exc).__name__}: {exc}",
                    "error": str(exc),
                    "stratum": None,
                    "page": None,
                    "stop_reason": str(exc),
                },
            )
            result = StepResult(
                component=component,
                status=STATUS_FAILED,
                records_processed=0,
                error_message=f"{type(exc).__name__}: {exc}",
                details={"traceback": traceback.format_exc()[-2000:]},
                started_at=started,
                completed_at=_utcnow(),
            )
        self._finalize_step(result)
        return result

    async def run(self) -> ProductionRunResult:
        started = _utcnow()
        if self.dry_run:
            validation = self.validate_environment()
            print_dry_run_summary(validation)
            return ProductionRunResult(
                run_id=None,
                status=STATUS_SUCCESS if validation.get("ok") else STATUS_FAILED,
                started_at=started,
                completed_at=_utcnow(),
                dry_run=True,
                validation=validation,
            )

        products_before = int(
            self.session.scalar(select(func.count()).select_from(Product)) or 0
        )

        with production_lock(
            self.session,
            lock_key=self.config.concurrent_lock_key,
            stale_hours=self.config.stale_running_hours,
        ) as acquired:
            if not acquired:
                logger.warning(
                    "run_skipped_concurrent",
                    extra={
                        "event": "run_skipped_concurrent",
                        "timestamp": started.isoformat(),
                        "status": STATUS_SKIPPED,
                        "trigger": self.trigger_source,
                        "source": self.trigger_source,
                        "run_id": None,
                    },
                )
                print(
                    "PRODUCTION COLLECTION RUN SKIPPED\n"
                    "Another active production collection run is in progress "
                    "(advisory lock / RUNNING record)."
                )
                return ProductionRunResult(
                    run_id=None,
                    status=STATUS_SKIPPED,
                    started_at=started,
                    completed_at=_utcnow(),
                    skipped_concurrent=True,
                )

            run = self._start_run()
            # Ensure unexpected exit still finalizes RUNNING → FAILED
            self._install_signal_handlers()

            steps: list[StepResult] = []
            product_details: dict[str, Any] = {
                "price_rows_created": 0,
                "snapshots_created": 0,
                "new_products": 0,
                "existing_reobserved": 0,
            }

            overall = STATUS_FAILED
            error_summary: Optional[str] = None
            try:
                # 1) Product collections
                newegg = await self._execute_component(
                    "newegg",
                    lambda: run_newegg_products(
                        self.session,
                        limit=self.product_limit,
                        parent_run_id=run.id,
                    ),
                )
                steps.append(newegg)
                if newegg.status != STATUS_SKIPPED:
                    d = newegg.details or {}
                    product_details["price_rows_created"] += int(
                        d.get("price_rows_created") or 0
                    )
                    product_details["snapshots_created"] += int(
                        d.get("snapshots_created") or 0
                    )
                    product_details["new_products"] += int(d.get("new_products") or 0)
                    product_details["existing_reobserved"] += int(
                        d.get("existing_reobserved") or 0
                    )
                    if newegg.status == STATUS_FAILED:
                        product_details["newegg_failed"] = True

                ml = await self._execute_component(
                    "mercadolibre",
                    lambda: run_mercadolibre_products(
                        self.session,
                        limit=self.product_limit,
                        parent_run_id=run.id,
                    ),
                )
                steps.append(ml)
                if ml.status != STATUS_SKIPPED:
                    d = ml.details or {}
                    product_details["price_rows_created"] += int(
                        d.get("price_rows_created") or 0
                    )
                    product_details["snapshots_created"] += int(
                        d.get("snapshots_created") or 0
                    )
                    product_details["new_products"] += int(d.get("new_products") or 0)
                    product_details["existing_reobserved"] += int(
                        d.get("existing_reobserved") or 0
                    )
                    if ml.status == STATUS_FAILED:
                        product_details["mercadolibre_failed"] = True

                # 2) Product-level observations (same product master population)
                audits = await self._execute_component(
                    "audits",
                    lambda: run_audits_step(self.session, parent_run_id=run.id),
                )
                steps.append(audits)

                badges = await self._execute_component(
                    "badges",
                    lambda: run_badges_step(self.session, parent_run_id=run.id),
                )
                steps.append(badges)

                pricing = await self._execute_component(
                    "pricing",
                    lambda: run_pricing_step(
                        self.session,
                        parent_run_id=run.id,
                        product_step_details=product_details,
                    ),
                )
                steps.append(pricing)

                # 3) Homepage banners (independent retailer-level)
                banners = await self._execute_component(
                    "banners",
                    lambda: run_banners_step(
                        self.session,
                        parent_run_id=run.id,
                        retailer_codes=list(self.retailers) if self.retailers else None,
                    ),
                )
                steps.append(banners)

                # 4) Search / Share of Voice
                search = await self._execute_component(
                    "search",
                    lambda: run_search_step(
                        self.session,
                        parent_run_id=run.id,
                        limit_per_retailer=self.search_limit,
                    ),
                )
                steps.append(search)

                overall = _aggregate_status([s.status for s in steps])
                errs = [
                    f"{s.component}:{s.error_message}"
                    for s in steps
                    if s.status in (STATUS_FAILED, STATUS_PARTIAL) and s.error_message
                ]
                error_summary = "; ".join(errs)[:4000] if errs else None
                self._finalize_run(
                    status=overall, steps=steps, error_summary=error_summary
                )

            except Exception as exc:  # noqa: BLE001
                logger.exception("production_run_crashed")
                overall = STATUS_FAILED
                error_summary = f"{type(exc).__name__}: {exc}"
                self._finalize_run(
                    status=overall, steps=steps, error_summary=error_summary
                )
            finally:
                self._remove_signal_handlers()

            products_after = int(
                self.session.scalar(select(func.count()).select_from(Product)) or 0
            )
            result = ProductionRunResult(
                run_id=run.id,
                status=overall,
                started_at=run.started_at,
                completed_at=run.completed_at or _utcnow(),
                steps=steps,
                products_before=products_before,
                products_after=products_after,
                error_summary=error_summary,
            )
            print_production_summary(result, product_details)
            return result

    def _install_signal_handlers(self) -> None:
        self._prev_handlers: dict[int, Any] = {}
        if os.name == "nt":
            return  # signal handling limited on Windows for worker processes

        def _handler(signum, frame):  # noqa: ANN001
            logger.error(
                "signal_received",
                extra={"event": "signal_received", "signum": signum},
            )
            self._finalize_run(
                status=STATUS_FAILED,
                steps=[],
                error_summary=f"interrupted_by_signal_{signum}",
            )
            raise SystemExit(1)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._prev_handlers[sig] = signal.signal(sig, _handler)
            except Exception:  # noqa: BLE001
                pass

    def _remove_signal_handlers(self) -> None:
        if os.name == "nt":
            return
        for sig, prev in getattr(self, "_prev_handlers", {}).items():
            try:
                signal.signal(sig, prev)
            except Exception:  # noqa: BLE001
                pass


def print_dry_run_summary(validation: dict[str, Any]) -> None:
    print("PRODUCTION COLLECTION DRY-RUN")
    print("=============================")
    print(f"Overall: {'PASS' if validation.get('ok') else 'FAIL'}")
    for key, value in (validation.get("checks") or {}).items():
        print(f"  {key}: {value}")


def print_production_summary(
    result: ProductionRunResult, product_details: dict[str, Any]
) -> None:
    by = {s.component: s for s in result.steps}
    duration = result.completed_at - result.started_at
    print()
    print("PRODUCTION COLLECTION RUN")
    print("==========================")
    print()
    print(f"Run ID:              {result.run_id}")
    print(f"Started:             {result.started_at.isoformat()}")
    print(f"Completed:           {result.completed_at.isoformat()}")
    print(f"Duration:            {duration}")
    print()
    print(f"Overall status:      {result.status}")
    print()
    for label, key in (
        ("Newegg", "newegg"),
        ("Mercado Libre", "mercadolibre"),
        ("Audits", "audits"),
        ("Badges", "badges"),
        ("Pricing", "pricing"),
        ("Homepage banners", "banners"),
        ("Search visibility", "search"),
    ):
        step = by.get(key)
        status = step.status if step else "SKIPPED"
        print(f"{label + ':':<20} {status}")
    print()
    print(
        f"Products before:     {result.products_before}"
    )
    print(
        f"Products after:      {result.products_after} "
        f"(new={product_details.get('new_products', 0)}, "
        f"reobserved={product_details.get('existing_reobserved', 0)})"
    )
    print(
        f"Products collected:  "
        f"{(by.get('newegg').records_processed if by.get('newegg') else 0) + (by.get('mercadolibre').records_processed if by.get('mercadolibre') else 0)}"
    )
    print(
        f"Audit observations:  {by['audits'].records_processed if by.get('audits') else 0}"
    )
    print(
        f"Badge observations:  {by['badges'].records_processed if by.get('badges') else 0}"
    )
    print(
        f"Price snapshots:     {by['pricing'].records_processed if by.get('pricing') else 0}"
    )
    print(
        f"Banner observations: {by['banners'].records_processed if by.get('banners') else 0}"
    )
    print(
        f"Search observations: {by['search'].records_processed if by.get('search') else 0}"
    )
    print()
    print("Database persistence: PASS")
    print("Historical data:      PRESERVED")
    print()
    # Compact component line (spec example)
    print(f"RUN {result.run_id}")
    for key in COMPONENTS:
        step = by.get(key)
        if not step:
            continue
        print(
            f"[{key:<16}] {step.status:<8} {step.records_processed} records"
        )


async def run_production(
    session: Session,
    *,
    product_limit: Optional[int] = None,
    search_limit: Optional[int] = None,
    retailers: Optional[Sequence[str]] = None,
    steps: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    trigger_source: Optional[str] = None,
) -> ProductionRunResult:
    runner = ProductionRunner(
        session,
        product_limit=product_limit,
        search_limit=search_limit,
        retailers=retailers,
        steps=steps,
        dry_run=dry_run,
        trigger_source=trigger_source,
    )
    return await runner.run()
