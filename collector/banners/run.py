"""CLI: homepage banner tracking against configured retailers.

Does not run product discovery or modify the product collector.

Usage:
    python -m collector.banners.run
    python -m collector.banners.run --retailer newegg
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter

from dotenv import load_dotenv

from collector.banners.collect import collect_homepage_banners
from collector.banners.detect import TRACKED_BRANDS
from collector.banners.persist import persist_banners
from collector.logging_utils import setup_logging
from database.connection import session_scope
from database.repositories import CollectionRunRepository

logger = logging.getLogger("collector.banners.run")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Homepage banner tracking")
    parser.add_argument(
        "--retailer",
        action="append",
        dest="retailers",
        help="Retailer code to inspect (repeatable). Default: all enabled.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect and report without writing to the database",
    )
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    setup_logging()
    results = await collect_homepage_banners(retailer_codes=args.retailers)

    total_banners = 0
    brand_counts: Counter[str] = Counter()
    screenshots = 0
    success = 0
    failed = 0

    print("Homepage Banner Tracking — collection")
    print("=====================================")
    print()

    with session_scope() as session:
        for result in results:
            status = "PASS" if result.inspected else "FAIL"
            if result.inspected:
                success += 1
            else:
                failed += 1

            brands = Counter(b.brand for b in result.banners)
            total_banners += len(result.banners)
            brand_counts.update(brands)
            if result.screenshot_path:
                screenshots += 1

            print(f"Retailer: {result.retailer_code} ({result.country_code})")
            print(f"Homepage inspected: {status}")
            if result.error:
                print(f"Reason: {result.error}")
            print(f"Homepage URL: {result.homepage_url}")
            print(f"Banners detected: {len(result.banners)}")
            for brand in TRACKED_BRANDS:
                print(f"  {brand}: {brands.get(brand, 0)}")
            unknown = brands.get("UNKNOWN", 0) + brands.get("AMBIGUOUS", 0)
            print(f"  Unknown/Ambiguous: {unknown}")
            print(f"Evidence/screenshots: {1 if result.screenshot_path else 0}")
            print()

            if args.dry_run or not result.inspected:
                continue

            runs = CollectionRunRepository(session)
            run = runs.start(
                retailer_code=result.retailer_code,
                country_code=result.country_code,
                run_type="banner",
                run_metadata={"source": "collector.banners", "page_type": "homepage"},
            )
            session.flush()
            rows = persist_banners(
                session,
                result.banners,
                retailer_code=result.retailer_code,
                country_code=result.country_code,
                collection_run_id=run.id,
                observed_at=result.observed_at,
            )
            runs.complete(
                run,
                status="completed",
                items_collected=len(rows),
                error_message=result.error,
            )
            print(f"Persisted {len(rows)} banner observation(s) for {result.retailer_code}")
            print()

    print("SUMMARY")
    print("-------")
    print(f"Retailers configured/attempted: {len(results)}")
    print(f"Successful inspections:         {success}")
    print(f"Failed inspections:             {failed}")
    print(f"Total banner observations:      {total_banners}")
    for brand in TRACKED_BRANDS:
        print(f"{brand}: {brand_counts.get(brand, 0)}")
    print(
        f"Unknown/Ambiguous: {brand_counts.get('UNKNOWN', 0) + brand_counts.get('AMBIGUOUS', 0)}"
    )
    print(f"Screenshots: {screenshots}")
    print()
    print("Command:")
    print("  python -m collector.banners.run")
    return 0 if failed == 0 or success > 0 else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
