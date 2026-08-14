"""Rebuild cross-retailer identity and report product visibility.

Usage:
  python -m analytics.product_visibility.run_existing
  python -m analytics.product_visibility.run_existing --rebuild-identity
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from analytics.product_identity import (
    crosswalk_summary,
    list_common_products,
    list_retailer_only_products,
    rebuild_cross_retailer_identity,
    retailer_product_counts,
)
from analytics.product_visibility import (
    highest_cross_retailer_visibility,
    highest_visibility_by_retailer,
)
from database.connection import get_engine, get_session_factory


def _fmt_vis(row) -> str:
    if row is None:
        return "n/a"
    return (
        f"{row.title or row.retailer_sku}\n"
        f"  Product ID: {row.product_id}\n"
        f"  Canonical Product ID: {row.canonical_product_id}\n"
        f"  Appearances: {row.appearances}\n"
        f"  Top-3/5/10/20: {row.top3_appearances}/{row.top5_appearances}/"
        f"{row.top10_appearances}/{row.top20_appearances}\n"
        f"  Average rank: {row.average_rank}\n"
        f"  Strata: {', '.join(row.strata) or 'n/a'}\n"
        f"  Collection: {row.collection_status}\n"
        f"  Source: {row.observation_source}\n"
        f"  Visibility score: {row.visibility_score}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Product identity + visibility report")
    parser.add_argument(
        "--rebuild-identity",
        action="store_true",
        help="Rebuild canonical_products / product_crosswalk from current products",
    )
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args(argv)

    load_dotenv()
    engine = get_engine()
    session = get_session_factory(engine)()
    try:
        if args.rebuild_identity:
            summary = rebuild_cross_retailer_identity(session)
            session.commit()
            print("Identity rebuild:", summary)
        else:
            # Ensure crosswalk exists for reporting
            from sqlalchemy import func, select
            from database.models import ProductCrosswalk

            n = session.scalar(select(func.count()).select_from(ProductCrosswalk)) or 0
            if int(n) == 0:
                summary = rebuild_cross_retailer_identity(session)
                session.commit()
                print("Identity rebuild (auto):", summary)

        counts = retailer_product_counts(session)
        print()
        print("Retailer product counts")
        print("-----------------------")
        print(f"Newegg: {counts.newegg}")
        print(f"Mercado Libre: {counts.mercadolibre}")
        print(f"Total retailer records: {counts.total_retailer_records}")
        print()
        print("Cross-retailer matching")
        print("-----------------------")
        cw = crosswalk_summary(session)
        print(f"Matched: {cw['matched']}")
        print(f"Possible matches: {cw['possible_matches']}")
        print(f"Unmatched: {cw['unmatched']}")
        print(f"Newegg-only: {cw['newegg_only']}")
        print(f"Mercado-Libre-only: {cw['mercadolibre_only']}")
        print(f"Common products: {cw['common_products']}")
        print(f"Unique canonical products: {cw['unique_canonical_products']}")

        print()
        print("Highest visibility — Newegg")
        print("---------------------------")
        for row in highest_visibility_by_retailer(
            session, "newegg", top_n=args.top
        ):
            print(_fmt_vis(row))
            print()

        print("Highest visibility — Mercado Libre")
        print("----------------------------------")
        for row in highest_visibility_by_retailer(
            session, "mercadolibre", top_n=args.top
        ):
            print(_fmt_vis(row))
            print()

        print("Highest combined visibility — MATCHED cross-retailer products")
        print("-------------------------------------------------------------")
        cross = highest_cross_retailer_visibility(session, top_n=args.top)
        if not cross:
            print(
                "No MATCHED cross-retailer pairs with visibility data. "
                "POSSIBLE_MATCH / UNMATCHED are excluded from definitive rankings."
            )
        for row in cross:
            print(f"Canonical Product: {row.display_name}")
            print(f"Canonical ID: {row.canonical_product_id}")
            print(f"Match: {row.match_status}  method={row.match_method}  "
                  f"confidence={row.match_confidence}")
            print(
                f"Newegg appearances: "
                f"{row.newegg_visibility.appearances if row.newegg_visibility else 0}"
            )
            print(
                f"Mercado Libre appearances: "
                f"{row.mercadolibre_visibility.appearances if row.mercadolibre_visibility else 0}"
            )
            print(f"Combined appearances: {row.combined_appearances}")
            print(f"Combined visibility score: {row.combined_visibility_score}")
            print()

        common = list_common_products(session)
        print(f"Common MATCHED products listed: {len(common)}")
        print(
            f"Newegg-only listed: {len(list_retailer_only_products(session, retailer_code='newegg'))}"
        )
        print(
            f"Mercado-Libre-only listed: "
            f"{len(list_retailer_only_products(session, retailer_code='mercadolibre'))}"
        )
        return 0
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
