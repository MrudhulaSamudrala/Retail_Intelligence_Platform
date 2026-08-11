"""CLI entrypoint: python -m collector.run --retailer newegg --limit 20"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Callable

from dotenv import load_dotenv

from collector.base import RetailerCollector
from collector.logging_utils import setup_logging
from collector.pipeline import CollectionPipeline
from database.connection import get_engine, get_session_factory


def _build_retailer(code: str) -> RetailerCollector:
    if code == "newegg":
        from collector.retailers.newegg import build_collector

        return build_collector()
    if code == "mercadolibre":
        raise NotImplementedError(
            "Mercado Libre collector is not implemented yet. "
            "Common pipeline is ready for a future adapter."
        )
    raise SystemExit(f"Unsupported retailer: {code}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BridgeAI retailer collection runner")
    parser.add_argument("--retailer", required=True, choices=["newegg", "mercadolibre"])
    parser.add_argument("--limit", type=int, default=20, help="Max products to collect")
    return parser.parse_args(argv)


async def _async_main(retailer: str, limit: int) -> int:
    logger = setup_logging()
    load_dotenv()
    collector = _build_retailer(retailer)
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
        "successful": len(outcome.success),
        "failed": len(outcome.failed),
        "skipped_duplicates": len(outcome.skipped_duplicates),
        "products": [
            {
                "sku": p.retailer_sku,
                "title": p.title,
                "brand": p.brand,
                "oem": p.oem,
                "product_type": p.product_type,
                "price": str(p.price_amount) if p.price_amount is not None else None,
                "currency": p.currency,
                "availability": p.availability,
                "url": p.source_url,
                "processor": p.processor,
                "gpu": p.gpu,
            }
            for p in outcome.success
        ],
        "errors": outcome.failed[:20],
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


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    raise SystemExit(asyncio.run(_async_main(args.retailer, args.limit)))


if __name__ == "__main__":
    main()
