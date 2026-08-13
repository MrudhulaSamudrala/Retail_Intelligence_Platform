"""CLI entrypoint: python -m collector.run --all

Production orchestration and selective retailer/step execution.
Also preserves legacy single-retailer product collection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional, Sequence

from dotenv import load_dotenv

from collector.logging_utils import setup_logging
from database.connection import get_engine, get_session_factory


STEP_CHOICES = (
    "newegg",
    "mercadolibre",
    "audits",
    "badges",
    "pricing",
    "banners",
    "search",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "BridgeAI production collection runner. "
            "Use --all for the full orchestrated pipeline."
        )
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run the full production orchestration pipeline once and exit",
    )
    parser.add_argument(
        "--retailer",
        choices=["newegg", "mercadolibre"],
        action="append",
        help="Limit product collection to retailer(s); can be repeated",
    )
    parser.add_argument(
        "--step",
        choices=STEP_CHOICES,
        action="append",
        help="Run only selected orchestration step(s); can be repeated",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max products per retailer (default from config/orchestration.yaml)",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=None,
        help="Max search queries per retailer",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate DB/config/Playwright without inserting observations",
    )
    parser.add_argument(
        "--legacy-product-only",
        action="store_true",
        help=(
            "Legacy mode: run only the product CollectionPipeline for --retailer "
            "(no production run tracking). Requires --retailer."
        ),
    )
    args = parser.parse_args(argv)
    if not args.all and not args.retailer and not args.step and not args.dry_run:
        parser.error("Specify --all, --retailer, --step, and/or --dry-run")
    if args.legacy_product_only and not args.retailer:
        parser.error("--legacy-product-only requires --retailer")
    return args


async def _legacy_product_run(retailer: str, limit: int) -> int:
    from collector.pipeline import CollectionPipeline

    if retailer == "newegg":
        from collector.retailers.newegg import build_collector
    elif retailer == "mercadolibre":
        from collector.retailers.mercadolibre import build_collector
    else:
        raise SystemExit(f"Unsupported retailer: {retailer}")

    logger = setup_logging()
    collector = build_collector()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    session = session_factory()
    try:
        pipeline = CollectionPipeline(session, collector)
        outcome = await pipeline.run(limit=limit)
    finally:
        session.close()
        engine.dispose()

    summary = {
        "retailer": retailer,
        "limit": limit,
        "collection_run_id": outcome.collection_run_id,
        "status": outcome.status,
        "discovered": outcome.discovered,
        "successful": len(outcome.success),
        "failed": len(outcome.failed),
        "skipped_duplicates": len(outcome.skipped_duplicates),
        "bot_blocked": outcome.bot_blocked,
    }
    print(json.dumps(summary, indent=2))
    logger.info(
        "run_summary",
        extra={
            "event": "run_summary",
            "retailer": retailer,
            "count": len(outcome.success),
        },
    )
    return 0 if outcome.success else 1


async def _orchestrated_main(args: argparse.Namespace) -> int:
    from collector.orchestration.runner import run_production

    setup_logging()
    load_dotenv()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    session = session_factory()
    try:
        retailers: Optional[Sequence[str]] = args.retailer
        steps: Optional[Sequence[str]] = args.step
        # --all with no filters → full pipeline
        if args.all and not retailers and not steps:
            retailers = None
            steps = None
        elif args.all and (retailers or steps):
            # --all plus filters still allowed (narrowed full-order run)
            pass
        elif not args.all and retailers and not steps:
            # product retailers only (skip banners/search unless also in --step)
            steps = list(retailers) + ["audits", "badges", "pricing"]
        elif not args.all and steps and not retailers:
            pass

        result = await run_production(
            session,
            product_limit=args.limit,
            search_limit=args.search_limit,
            retailers=retailers,
            steps=steps,
            dry_run=args.dry_run,
        )
        return result.exit_code
    finally:
        session.close()
        engine.dispose()


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    if args.legacy_product_only:
        limit = args.limit if args.limit is not None else 20
        code = asyncio.run(_legacy_product_run(args.retailer[0], limit))
        raise SystemExit(code)
    raise SystemExit(asyncio.run(_orchestrated_main(args)))


if __name__ == "__main__":
    main()
