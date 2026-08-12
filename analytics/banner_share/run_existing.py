"""CLI: calculate Banner Share from existing banner_observations.

Usage:
    python -m analytics.banner_share.run_existing
"""

from __future__ import annotations

from dotenv import load_dotenv
from sqlalchemy import func, select

from analytics.banner_share import (
    BannerShareScope,
    banner_share_by_brand,
    banner_share_trends,
)
from collector.banners.detect import TRACKED_BRANDS
from database.connection import session_scope
from database.models import BannerObservation


def _pct(share) -> str:
    return f"{float(share) * 100:.2f}%"


def main() -> int:
    load_dotenv()
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(BannerObservation)) or 0
        snap = banner_share_by_brand(session)
        trends = banner_share_trends(session)

        print("BANNER SHARE")
        print("============")
        print(f"Total homepage banner observations: {snap.total_observations}")
        print(f"Tracked-brand denominator:          {snap.total_tracked_banners}")
        print(f"Unknown/Ambiguous (excluded):       {snap.unknown_or_ambiguous}")
        print(
            f"include_unknown_in_denominator:     {snap.include_unknown_in_denominator}"
        )
        print()
        print("By brand:")
        for row in snap.shares:
            print(
                f"  {row.brand:12} count={row.banner_count}  "
                f"share={_pct(row.banner_share)}"
            )
        print()

        retailers = session.scalars(
            select(BannerObservation.retailer_code).distinct()
        ).all()
        print("By retailer:")
        for retailer in retailers:
            rsnap = banner_share_by_brand(
                session, scope=BannerShareScope(retailer_code=retailer)
            )
            print(f"  {retailer}  tracked={rsnap.total_tracked_banners}")
            for row in rsnap.shares:
                if row.banner_count:
                    print(f"    {row.brand:12} {_pct(row.banner_share)}")
        print()

        print("Historical trends:")
        if not trends:
            print("  (none)")
        else:
            current_key = None
            for point in trends:
                key = (point.period_start.date(), point.retailer_code)
                if key != current_key:
                    current_key = key
                    print(
                        f"  {point.period_start.date()}  retailer={point.retailer_code}  "
                        f"total_tracked={point.total_tracked_banners}"
                    )
                if point.banner_count or point.brand in TRACKED_BRANDS:
                    print(
                        f"    {point.brand:12} n={point.banner_count}  "
                        f"share={_pct(point.banner_share)}"
                    )
        print()
        print(f"Table rows in banner_observations: {total}")
        print("Command: python -m analytics.banner_share.run_existing")
        print("API: banner_share_by_brand(session); banner_share_trends(session)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
