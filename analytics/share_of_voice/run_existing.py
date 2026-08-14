"""CLI: compute Share of Voice / search visibility from existing DB rows.

Usage:
    python -m analytics.share_of_voice.run_existing
"""

from __future__ import annotations

from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy import func, select

from analytics.share_of_voice import SovScope, keyword_metrics, share_of_voice
from collector.search.config import load_keyword_targets, load_sov_config
from database.connection import session_scope
from database.models import SearchObservation


def _pct(share) -> str:
    return f"{float(share) * 100:.2f}%"


def main() -> int:
    load_dotenv()
    cfg = load_sov_config()
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(SearchObservation)) or 0
        snap = share_of_voice(session)
        presence = {m.brand: m.present for m in snap.metrics}
        appearances = {m.brand: m.appearances for m in snap.metrics}

        print("Share of Voice / Search Visibility")
        print("==================================")
        print(f"observation_source: {snap.observation_source}")
        print(f"collection_basis: {snap.collection_basis}")
        if snap.collection_basis != "exact":
            print(
                "NOTE: Not all searches are COMPLETE — treat SoV as "
                "observed/partial search visibility, not exact full-universe SoV."
            )
        print(f"Result observations (latest batches): {snap.total_observations}")
        print(f"Eligible observations: {snap.eligible_observations}")
        print(f"Tracked-brand results: {snap.tracked_appearances}")
        print(f"UNKNOWN results: {snap.unknown_appearances}")
        print(f"OTHER results: {snap.other_appearances}")
        print(f"Excluded observations: {snap.excluded_observations}")
        print(f"Duplicate observations: {snap.duplicate_observations}")
        print(
            f"Searches COMPLETE/PARTIAL/FAILED: "
            f"{snap.complete_searches}/{snap.partial_searches}/{snap.failed_searches}"
        )
        print()
        print("Brand Presence:")
        for brand in cfg.tracked_brands:
            print(f"  {brand:10} {'present' if presence.get(brand) else 'absent'}")
        print()
        print("Appearances:")
        for brand in cfg.tracked_brands:
            print(f"  {brand:10} {appearances.get(brand, 0)}")
        print()

        for n in cfg.top_n_options:
            print(f"Top-{n}:")
            metrics = keyword_metrics(session, top_n=n)
            for m in metrics:
                if m.brand in cfg.tracked_brands:
                    print(f"  {m.brand:10} {m.top_n_count}")
            print()

        print("Average Ranking (observed positions only):")
        for m in snap.metrics:
            avg = "n/a" if m.average_rank is None else str(m.average_rank)
            print(
                f"  {m.brand:10} {avg}  (n={m.rank_observation_count})"
            )
        print()
        print("Share of Voice (tracked-brand denominator):")
        for m in snap.metrics:
            print(f"  {m.brand:10} {_pct(m.share_of_voice)}")
        print()
        if snap.stratum_status:
            print("Stratum status:")
            for name, status in snap.stratum_status.items():
                print(f"  {name:12} {status}")
            print()
        if snap.stratum_metrics:
            print("Stratum Share of Voice:")
            current = None
            for m in snap.stratum_metrics:
                if m.stratum != current:
                    current = m.stratum
                    print(f"  {current}:")
                print(
                    f"    {m.brand:10} {_pct(m.share_of_voice)} "
                    f"(appearances={m.appearances})"
                )
            print()

        # Keyword-level
        keywords = session.scalars(
            select(SearchObservation.keyword).distinct()
        ).all()
        print("Keyword-level:")
        for kw in keywords:
            print(f"  Keyword: {kw}")
            for m in keyword_metrics(session, scope=SovScope(keyword=kw)):
                if m.brand not in cfg.tracked_brands:
                    continue
                avg = "n/a" if m.average_rank is None else str(m.average_rank)
                print(
                    f"    {m.brand}: appearances={m.appearances} "
                    f"Top-{m.top_n}={m.top_n_count} avg_rank={avg} "
                    f"SOV={_pct(m.share_of_voice)} [{m.collection_basis}]"
                )
        print()
        print(f"Table rows in search_observations: {total}")
        print(f"Keywords configured: {len(load_keyword_targets())}")
        print("Command: python -m analytics.share_of_voice.run_existing")
        print("API: share_of_voice(session); keyword_metrics(session, scope=SovScope(...))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
