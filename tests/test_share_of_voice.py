"""Controlled Share of Voice / search visibility tests (no live websites)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.share_of_voice import (
    SovScope,
    brand_presence,
    keyword_metrics,
    share_of_voice,
    share_of_voice_trends,
)
from collector.search.config import load_keyword_targets, load_sov_config
from collector.search.models import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_ZERO,
    SearchHit,
    SearchRunResult,
)
from collector.search.persist import persist_search_run
from database.models import Base, SearchObservation
from database.repositories import ObservationRepository


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _hit(
    *,
    keyword: str = "gaming laptop",
    retailer: str = "newegg",
    country: str = "US",
    position: int,
    page: int = 1,
    brand: str,
    sku: str | None = None,
    title: str | None = None,
    sponsored: bool = False,
) -> SearchHit:
    return SearchHit(
        keyword=keyword,
        retailer_code=retailer,
        country_code=country,
        position=position,
        page_number=page,
        retailer_sku=sku or f"SKU-{brand}-{position}",
        source_url=f"https://example.test/{sku or position}",
        title=title or f"{brand} Gaming Laptop {position}",
        brand=brand,
        oem="Asus",
        is_sponsored=sponsored,
        evidence_text=title or f"{brand} Gaming Laptop {position}",
        selector=".item-cell",
        search_url="https://example.test/search",
    )


def _seed_complete_search(session: Session) -> None:
    """
    Known universe for keyword 'gaming laptop' (COMPLETE):
    pos1 Intel, 2 AMD, 3 Apple, 4 Intel, 5 AMD, 6 Qualcomm,
    7 Intel, 8 AMD, 9 Apple, 10 Intel, 11 UNKNOWN, 12 AMD
    """
    brands = [
        "Intel",
        "AMD",
        "Apple",
        "Intel",
        "AMD",
        "Qualcomm",
        "Intel",
        "AMD",
        "Apple",
        "Intel",
        "UNKNOWN",
        "AMD",
    ]
    hits = [
        _hit(position=i + 1, page=1 if i < 10 else 2, brand=b)
        for i, b in enumerate(brands)
    ]
    run = SearchRunResult(
        retailer_code="newegg",
        country_code="US",
        keyword="gaming laptop",
        collection_status=STATUS_COMPLETE,
        pages_collected=2,
        hits=hits,
        search_url="https://www.newegg.com/p/pl?d=gaming+laptop",
        observed_at=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        pagination_reliable=True,
    )
    persist_search_run(session, run)
    session.commit()


def test_keywords_config_is_multi_retailer_country() -> None:
    targets = load_keyword_targets()
    assert any(t.retailer_code == "newegg" and t.country_code == "US" for t in targets)
    assert any(
        t.retailer_code == "mercadolibre" and t.country_code == "BR" for t in targets
    )
    assert len({t.keyword for t in targets}) >= 2
    cfg = load_sov_config()
    assert 3 in cfg.top_n_options and 10 in cfg.top_n_options
    assert cfg.include_unknown_in_denominator is False


def test_brand_presence_appearances_topn_avg_sov(session: Session) -> None:
    _seed_complete_search(session)
    presence = brand_presence(session, scope=SovScope(keyword="gaming laptop"))
    assert presence["Intel"] is True
    assert presence["AMD"] is True
    assert presence["Apple"] is True
    assert presence["Qualcomm"] is True

    metrics = {m.brand: m for m in keyword_metrics(session, scope=SovScope(keyword="gaming laptop"), top_n=10)}
    # Appearances: Intel 4, AMD 4, Apple 2, Qualcomm 1; UNKNOWN excluded from denom
    # denom tracked = 11
    assert metrics["Intel"].appearances == 4
    assert metrics["AMD"].appearances == 4
    assert metrics["Apple"].appearances == 2
    assert metrics["Qualcomm"].appearances == 1
    assert metrics["Intel"].total_tracked_appearances == 11

    # Top-10 counts (positions 1-10): Intel 1,4,7,10 → 4; AMD 2,5,8 → 3; Apple 3,9 → 2; Qualcomm 6 → 1
    assert metrics["Intel"].top_n_count == 4
    assert metrics["AMD"].top_n_count == 3
    assert metrics["Apple"].top_n_count == 2
    assert metrics["Qualcomm"].top_n_count == 1

    # Avg Intel positions 1,4,7,10 → 5.5
    assert metrics["Intel"].average_rank == Decimal("5.50")
    assert metrics["Intel"].rank_observation_count == 4

    # SoV Intel 4/11
    assert metrics["Intel"].share_of_voice == Decimal("0.3636")
    assert metrics["AMD"].share_of_voice == Decimal("0.3636")
    assert metrics["Apple"].share_of_voice == Decimal("0.1818")
    assert metrics["Qualcomm"].share_of_voice == Decimal("0.0909")
    assert metrics["Intel"].collection_basis == "exact"


def test_configurable_top_n(session: Session) -> None:
    _seed_complete_search(session)
    top3 = {m.brand: m.top_n_count for m in keyword_metrics(session, top_n=3)}
    top5 = {m.brand: m.top_n_count for m in keyword_metrics(session, top_n=5)}
    # Top3: Intel, AMD, Apple
    assert top3["Intel"] == 1
    assert top3["AMD"] == 1
    assert top3["Apple"] == 1
    assert top3["Qualcomm"] == 0
    # Top5 adds Intel@4, AMD@5
    assert top5["Intel"] == 2
    assert top5["AMD"] == 2


def test_unknown_excluded_from_sov_denominator(session: Session) -> None:
    _seed_complete_search(session)
    snap = share_of_voice(session, scope=SovScope(keyword="gaming laptop"))
    assert snap.unknown_appearances == 1
    assert snap.tracked_appearances == 11
    total_share = sum(m.share_of_voice for m in snap.metrics)
    assert total_share == Decimal("0.9999") or abs(total_share - Decimal("1")) < Decimal("0.01")


def test_multiple_retailers_countries_keywords(session: Session) -> None:
    t0 = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    for retailer, country, keyword, brand in [
        ("newegg", "US", "gaming laptop", "Intel"),
        ("newegg", "US", "gaming desktop", "AMD"),
        ("mercadolibre", "BR", "notebook gamer", "Apple"),
    ]:
        persist_search_run(
            session,
            SearchRunResult(
                retailer_code=retailer,
                country_code=country,
                keyword=keyword,
                collection_status=STATUS_COMPLETE,
                pages_collected=1,
                observed_at=t0,
                hits=[
                    _hit(
                        keyword=keyword,
                        retailer=retailer,
                        country=country,
                        position=1,
                        brand=brand,
                    )
                ],
            ),
        )
    session.commit()

    us = share_of_voice(session, scope=SovScope(country_code="US"))
    assert us.tracked_appearances == 2
    br = brand_presence(session, scope=SovScope(country_code="BR"))
    assert br["Apple"] is True
    assert br["Intel"] is False


def test_duplicate_position_not_double_counted(session: Session) -> None:
    t0 = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    hit = _hit(position=1, brand="Intel")
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="gaming laptop",
            collection_status=STATUS_COMPLETE,
            pages_collected=1,
            observed_at=t0,
            hits=[hit, hit],  # accidental duplicate
        ),
    )
    session.commit()
    snap = share_of_voice(session, scope=SovScope(keyword="gaming laptop"))
    assert snap.total_observations == 1
    assert snap.metrics[0].appearances == 1 if snap.metrics[0].brand == "Intel" else True
    intel = next(m for m in snap.metrics if m.brand == "Intel")
    assert intel.appearances == 1


def test_historical_observations_append_only(session: Session) -> None:
    t0 = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    for ts, brand in [(t0, "Intel"), (t1, "AMD")]:
        persist_search_run(
            session,
            SearchRunResult(
                retailer_code="newegg",
                country_code="US",
                keyword="gaming laptop",
                collection_status=STATUS_COMPLETE,
                pages_collected=1,
                observed_at=ts,
                hits=[_hit(position=1, brand=brand)],
            ),
        )
    session.commit()
    rows = session.scalars(
        select(SearchObservation).order_by(SearchObservation.observed_at.asc())
    ).all()
    assert len(rows) == 2
    assert rows[0].brand == "Intel"
    assert rows[1].brand == "AMD"

    trends = share_of_voice_trends(
        session, scope=SovScope(keyword="gaming laptop", retailer_code="newegg")
    )
    days = sorted({p.period_start.date() for p in trends})
    assert len(days) == 2


def test_partial_and_failed_and_zero_searches(session: Session) -> None:
    t0 = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="gaming laptop",
            collection_status=STATUS_PARTIAL,
            pages_collected=1,
            observed_at=t0,
            pagination_reliable=False,
            hits=[_hit(position=1, brand="Intel"), _hit(position=2, brand="AMD")],
        ),
    )
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="missing results",
            collection_status=STATUS_ZERO,
            pages_collected=1,
            observed_at=t0,
            hits=[],
        ),
    )
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="blocked",
            collection_status=STATUS_FAILED,
            pages_collected=0,
            observed_at=t0,
            error="bot_challenge",
            hits=[],
        ),
    )
    session.commit()

    snap = share_of_voice(session, scope=SovScope(keyword="gaming laptop"))
    assert snap.collection_basis == "observed_partial"
    assert snap.partial_searches == 1

    exact_only = share_of_voice(
        session,
        scope=SovScope(keyword="gaming laptop", require_complete=True),
    )
    assert exact_only.total_observations == 0


def test_average_rank_ignores_missing_brands(session: Session) -> None:
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="gaming laptop",
            collection_status=STATUS_COMPLETE,
            pages_collected=1,
            observed_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            hits=[
                _hit(position=1, brand="Intel"),
                _hit(position=3, brand="Intel"),
                _hit(position=7, brand="Intel"),
                _hit(position=9, brand="Intel"),
                _hit(position=2, brand="AMD"),
            ],
        ),
    )
    session.commit()
    metrics = {m.brand: m for m in keyword_metrics(session)}
    assert metrics["Intel"].average_rank == Decimal("5.00")
    assert metrics["Qualcomm"].average_rank is None
    assert metrics["Qualcomm"].rank_observation_count == 0
