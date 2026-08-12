"""CLI: Share of Voice / search visibility collection.

Does not modify the product collection pipeline.

Usage:
    python -m collector.search.run
    python -m collector.search.run --retailer newegg --limit-per-retailer 2
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter

from dotenv import load_dotenv

from collector.logging_utils import setup_logging
from collector.search.collect import collect_search_visibility
from collector.search.config import load_keyword_targets, load_sov_config
from collector.search.persist import persist_search_run
from database.connection import session_scope

logger = logging.getLogger("collector.search.run")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Search visibility / SoV collection")
    p.add_argument("--retailer", action="append", dest="retailers")
    p.add_argument("--country", action="append", dest="countries")
    p.add_argument("--keyword", action="append", dest="keywords")
    p.add_argument(
        "--limit-per-retailer",
        type=int,
        default=None,
        help="Optional cap on keywords per retailer (for smoke runs)",
    )
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    setup_logging()
    cfg = load_sov_config()
    targets = load_keyword_targets(
        retailer_codes=args.retailers,
        country_codes=args.countries,
        keywords=args.keywords,
        limit_per_retailer=args.limit_per_retailer,
    )
    print("Share of Voice / Search Visibility — collection")
    print("==============================================")
    print(f"Configured targets: {len(targets)}")
    print(f"max_pages={cfg.max_pages} max_results={cfg.max_results_per_keyword}")
    print()

    results = await collect_search_visibility(
        retailer_codes=args.retailers,
        country_codes=args.countries,
        keywords=args.keywords,
        limit_per_retailer=args.limit_per_retailer,
    )

    status_counts: Counter[str] = Counter()
    brand_counts: Counter[str] = Counter()
    total_hits = 0
    unique_skus: set[str] = set()

    with session_scope() as session:
        for run in results:
            status_counts[run.collection_status] += 1
            total_hits += len(run.hits)
            for hit in run.hits:
                brand_counts[hit.brand or "UNKNOWN"] += 1
                if hit.retailer_sku:
                    unique_skus.add(f"{hit.retailer_code}:{hit.retailer_sku}")

            print(
                f"{run.retailer_code}/{run.country_code} | {run.keyword!r} | "
                f"status={run.collection_status} pages={run.pages_collected} "
                f"results={len(run.hits)}"
            )
            if run.error:
                print(f"  error: {run.error}")

            if not args.dry_run:
                n = persist_search_run(session, run)
                print(f"  persisted={n}")

    tracked = sum(brand_counts.get(b, 0) for b in cfg.tracked_brands)
    unknown = brand_counts.get("UNKNOWN", 0)
    print()
    print("SUMMARY")
    print("-------")
    print(f"Searches attempted: {len(results)}")
    print(f"COMPLETE: {status_counts.get('COMPLETE', 0)}")
    print(f"PARTIAL:  {status_counts.get('PARTIAL', 0)}")
    print(f"FAILED:   {status_counts.get('FAILED', 0)}")
    print(f"ZERO:     {status_counts.get('ZERO_RESULTS', 0)}")
    print(f"Result observations: {total_hits}")
    print(f"Unique products (sku): {len(unique_skus)}")
    print(f"Tracked-brand observations: {tracked}")
    print(f"UNKNOWN observations: {unknown}")
    print("Command: python -m collector.search.run")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    return asyncio.run(_async_main(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
