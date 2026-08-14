"""Run Share of Shelf analytics against existing PostgreSQL products.

Uses the live ``products`` table (and ``product_snapshots`` for trends).
Does not collect new products or launch Playwright.

Usage (from repo root):

    python -m analytics.share_of_shelf.run_existing
"""

from __future__ import annotations

from collections import Counter

from dotenv import load_dotenv
from sqlalchemy import func, select

from analytics.share_of_shelf import (
    INCLUSION_RULES_ID,
    SosScope,
    load_sos_universe_config,
    share_of_shelf_by_brand,
    share_of_shelf_by_oem,
    share_of_shelf_trends,
)
from analytics.share_of_shelf.queries import load_eligible_listings
from analytics.share_of_shelf.universe import evaluate_listing_eligibility
from database.connection import session_scope
from database.models import Product, ProductSnapshot


def _pct(share) -> str:
    if share is None:
        return "n/a"
    return f"{float(share) * 100:.2f}%"


def _print_shares(title: str, shares) -> None:
    print(title)
    if not shares:
        print("  (none)")
        return
    for row in shares:
        print(
            f"  {row.value:16} count={row.product_count:4d}  "
            f"share={_pct(row.share)}  (universe={row.universe_size})"
        )


def main() -> int:
    load_dotenv()
    cfg = load_sos_universe_config()

    with session_scope() as session:
        total_products = session.scalar(select(func.count()).select_from(Product)) or 0
        active_products = (
            session.scalar(
                select(func.count()).select_from(Product).where(Product.is_active.is_(True))
            )
            or 0
        )
        snapshot_count = (
            session.scalar(select(func.count()).select_from(ProductSnapshot)) or 0
        )

        # Inventory of all active products before eligibility filter
        all_active = session.scalars(
            select(Product).where(Product.is_active.is_(True))
        ).all()

        exclusion_reasons: Counter[str] = Counter()
        for p in all_active:
            ok, reason = evaluate_listing_eligibility(
                product_type=p.product_type,
                title=p.title,
                category_raw=p.category_raw,
                config=cfg,
            )
            if not ok:
                exclusion_reasons[reason or "unknown"] += 1

        listings, exclusions, _batch = load_eligible_listings(session, scope=SosScope())
        brand_sos = share_of_shelf_by_brand(session)
        oem_sos = share_of_shelf_by_oem(session)

        # Dimension breakdowns: recompute brand SoS within each slice
        retailers = sorted({p.retailer_code for p in all_active})
        countries = sorted({p.country_code for p in all_active})
        product_types = sorted(
            {p.product_type for p in all_active if p.product_type}
        )

        print("SHARE OF SHELF (existing database)")
        print("==================================")
        print()
        print(f"Inclusion rules id: {INCLUSION_RULES_ID}")
        print(f"Eligible product types: {sorted(cfg.eligible_product_types)}")
        print(f"Include out of stock: {cfg.include_out_of_stock}")
        print(f"Deduplicate by: {cfg.deduplicate_by}")
        print()
        print("INVENTORY")
        print("---------")
        print(f"Products (total):              {total_products}")
        print(f"Products (active):             {active_products}")
        print(f"Product snapshots (historical):{snapshot_count}")
        print(f"Eligible universe size:        {brand_sos.universe_size}")
        print(f"Collection status:             {brand_sos.collection_status}")
        print(f"Excluded from universe:        {active_products - brand_sos.universe_size}")
        print("Exclusion reasons (active products):")
        if exclusion_reasons:
            for reason, count in sorted(exclusion_reasons.items()):
                print(f"  - {reason}: {count}")
        else:
            print("  (none)")
        print(
            f"  (engine breakdown) accessory/ineligible_type="
            f"{exclusions.accessory_or_ineligible_type}, "
            f"non_gaming={exclusions.non_gaming}, "
            f"missing_identity={exclusions.missing_identity}"
        )
        print()

        print("Eligible listings (detail):")
        if not listings:
            print("  (empty universe)")
        else:
            for item in sorted(
                listings, key=lambda x: (x.brand or "", x.retailer_sku)
            ):
                print(
                    f"  [{item.brand or 'UNKNOWN':9}] "
                    f"oem={item.oem or '-':8} type={item.product_type or '-':12} "
                    f"{item.retailer_code}/{item.country_code} sku={item.retailer_sku}"
                )
        print()

        _print_shares("Brand Share of Shelf:", brand_sos.shares)
        print()
        _print_shares("OEM drilldown (same universe):", oem_sos.shares)
        print()

        print("Retailer breakdown (brand SoS within retailer):")
        for retailer in retailers:
            snap = share_of_shelf_by_brand(
                session, scope=SosScope(retailer_code=retailer)
            )
            print(f"  retailer={retailer}  universe={snap.universe_size}")
            for row in snap.shares:
                print(f"    {row.value:12} {_pct(row.share)} (n={row.product_count})")
        print()

        print("Country breakdown (brand SoS within country):")
        for country in countries:
            snap = share_of_shelf_by_brand(
                session, scope=SosScope(country_code=country)
            )
            print(f"  country={country}  universe={snap.universe_size}")
            for row in snap.shares:
                print(f"    {row.value:12} {_pct(row.share)} (n={row.product_count})")
        print()

        print("Product-type breakdown (brand SoS within type):")
        for ptype in product_types:
            snap = share_of_shelf_by_brand(
                session, scope=SosScope(product_type=ptype)
            )
            print(f"  product_type={ptype}  universe={snap.universe_size}")
            if not snap.shares:
                print("    (no eligible listings)")
            for row in snap.shares:
                print(f"    {row.value:12} {_pct(row.share)} (n={row.product_count})")
        print()

        print("OEM-filtered brand SoS (per OEM present in eligible universe):")
        oems = sorted({item.oem for item in listings if item.oem})
        if not oems:
            print("  (no OEMs in eligible universe)")
        for oem in oems:
            snap = share_of_shelf_by_brand(session, scope=SosScope(oem=oem))
            print(f"  oem={oem}  universe={snap.universe_size}")
            for row in snap.shares:
                print(f"    {row.value:12} {_pct(row.share)} (n={row.product_count})")
        print()

        print("Historical trends (daily as-of from product_snapshots):")
        trends = share_of_shelf_trends(session)
        if not trends:
            print("  (no snapshot history available)")
        else:
            for point in trends:
                print(
                    f"  {point.period_start.date()}  universe={point.universe_size}"
                )
                for row in point.shares:
                    print(
                        f"    {row.value:12} {_pct(row.share)} (n={row.product_count})"
                    )
        print()

        print("REPRODUCE")
        print("---------")
        print("Command:")
        print("  python -m analytics.share_of_shelf.run_existing")
        print()
        print("API:")
        print("  from analytics.share_of_shelf import (")
        print("      SosScope, share_of_shelf_by_brand, share_of_shelf_by_oem,")
        print("      share_of_shelf_trends,")
        print("  )")
        print("  share_of_shelf_by_brand(session)")
        print("  share_of_shelf_by_oem(session)")
        print("  share_of_shelf_by_brand(session, scope=SosScope(retailer_code=...))")
        print("  share_of_shelf_by_brand(session, scope=SosScope(country_code=...))")
        print("  share_of_shelf_by_brand(session, scope=SosScope(product_type=...))")
        print("  share_of_shelf_by_brand(session, scope=SosScope(oem=...))")
        print("  share_of_shelf_trends(session)")
        print()
        print("SQL (candidate inventory):")
        print("  SELECT COUNT(*) FROM products WHERE is_active = TRUE;")
        print(
            "  SELECT brand, oem, product_type, title, category_raw "
            "FROM products WHERE is_active = TRUE;"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
