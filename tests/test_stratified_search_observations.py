"""Stratified catalog search_observations persistence (no live websites)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from collector.search.models import STATUS_COMPLETE, SearchHit, SearchRunResult
from collector.search.persist import (
    SOURCE_KEYWORD_SEARCH,
    SOURCE_STRATIFIED_CATALOG,
    is_stratified_catalog_observation,
    persist_search_run,
    persist_stratified_catalog_observations,
    stratum_observation_status,
)
from database.models import Base, SearchObservation
from database.repositories import CollectionRunRepository, ProductRepository


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


def _run(session: Session) -> int:
    row = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="catalog"
    )
    session.flush()
    return int(row.id)


def _slot(
    *,
    stratum: str,
    query: str,
    position: int,
    sku: str | None,
    title: str,
    bucket: str,
    page: int = 1,
    universe_slot: int | None = None,
    brand: str | None = "AMD",
    oem: str | None = "Asus",
) -> dict:
    return {
        "stratum": stratum,
        "query": query,
        "search_position": position,
        "search_page": page,
        "universe_slot": universe_slot if universe_slot is not None else position + 100,
        "sku": sku,
        "title": title,
        "bucket": bucket,
        "extraction_status": "EXTRACTED" if bucket in {"VALID", "EXCLUDED"} else bucket,
        "brand": brand,
        "oem": oem,
        "url": f"https://www.newegg.com/p/{sku or position}",
        "is_sponsored": False,
        "used_fallback": False,
        "gaming": bucket == "VALID",
    }


def test_six_strata_preserve_native_positions(session: Session) -> None:
    run_id = _run(session)
    queries = {
        "notebook": "gaming laptop",
        "desktop": "gaming desktop",
        "workstation": "gaming workstation",
        "tablet": "gaming tablet",
        "gpu": "gaming graphics card",
        "cpu": "gaming processor",
    }
    slots = [
        _slot(
            stratum=name,
            query=query,
            position=1,
            sku=f"SKU-{name}-1",
            title=f"Gaming {name} product",
            bucket="VALID",
            universe_slot=idx + 1,
        )
        for idx, (name, query) in enumerate(queries.items())
    ]
    reports = [
        {
            "stratum": name,
            "query": query,
            "observed": 1,
            "requested": 20 if name not in {"gpu", "cpu"} else 10,
            "completeness": "COMPLETE",
            "search_status": "OK",
            "used_fallback": False,
        }
        for name, query in queries.items()
    ]
    n = persist_stratified_catalog_observations(
        session,
        collection_run_id=run_id,
        retailer_code="newegg",
        country_code="US",
        slots=slots,
        strata_reports=reports,
    )
    session.commit()
    assert n == 6
    rows = session.scalars(select(SearchObservation)).all()
    assert {r.stratum for r in rows} == set(queries)
    assert {r.keyword for r in rows} == set(queries.values())
    assert all(r.position == 1 for r in rows)
    assert all(r.observation_source == SOURCE_STRATIFIED_CATALOG for r in rows)
    assert all(r.collection_status == "COMPLETE" for r in rows)
    # universe_slot must not become position
    assert all((r.details or {}).get("universe_slot") != 1 or r.stratum == "notebook" for r in rows)
    cpu = next(r for r in rows if r.stratum == "cpu")
    assert cpu.details["universe_slot"] == 6
    assert cpu.position == 1


def test_excluded_and_duplicate_keep_native_position(session: Session) -> None:
    run_id = _run(session)
    slots = [
        _slot(
            stratum="workstation",
            query="gaming workstation",
            position=1,
            sku="WS-PC-1",
            title="Dell Precision Gaming Workstation",
            bucket="VALID",
            universe_slot=41,
        ),
        _slot(
            stratum="workstation",
            query="gaming workstation",
            position=7,
            sku="DESK-7",
            title='Electric RGB Gaming Standing Desk 55"',
            bucket="EXCLUDED",
            universe_slot=47,
            brand="UNKNOWN",
            oem="UNKNOWN",
        ),
        _slot(
            stratum="workstation",
            query="gaming workstation",
            position=8,
            sku="WS-PC-1",
            title="Dell Precision Gaming Workstation",
            bucket="DUPLICATE",
            universe_slot=48,
        ),
    ]
    reports = [
        {
            "stratum": "workstation",
            "query": "gaming workstation",
            "observed": 8,
            "requested": 20,
            "completeness": "PARTIAL",
            "search_status": "OK",
            "used_fallback": False,
        }
    ]
    persist_stratified_catalog_observations(
        session,
        collection_run_id=run_id,
        retailer_code="newegg",
        country_code="US",
        slots=slots,
        strata_reports=reports,
    )
    session.commit()
    rows = list(session.scalars(select(SearchObservation).order_by(SearchObservation.position)))
    positions = [r.position for r in rows]
    assert positions == [1, 7, 8]
    desk = next(r for r in rows if r.position == 7)
    assert desk.details["excluded"] is True
    assert desk.details["slot_status"] == "EXCLUDED"
    dup = next(r for r in rows if r.position == 8)
    assert dup.details["duplicate"] is True
    assert dup.retailer_sku == "WS-PC-1"
    assert all(r.collection_status == "PARTIAL" for r in rows)


def test_positions_not_renumbered_after_filtering(session: Session) -> None:
    run_id = _run(session)
    slots = [
        _slot(stratum="tablet", query="gaming tablet", position=p, sku=f"T-{p}", title=f"item {p}", bucket=b)
        for p, b in ((1, "VALID"), (2, "EXCLUDED"), (3, "VALID"))
    ]
    persist_stratified_catalog_observations(
        session,
        collection_run_id=run_id,
        retailer_code="newegg",
        country_code="US",
        slots=slots,
        strata_reports=[
            {
                "stratum": "tablet",
                "query": "gaming tablet",
                "observed": 3,
                "completeness": "COMPLETE",
                "search_status": "OK",
                "used_fallback": False,
            }
        ],
    )
    session.commit()
    assert [r.position for r in session.scalars(select(SearchObservation).order_by(SearchObservation.position))] == [
        1,
        2,
        3,
    ]


def test_fallback_is_partial_never_complete() -> None:
    status = stratum_observation_status(
        {
            "completeness": "COMPLETE",
            "search_status": "BLOCKED",
            "used_fallback": True,
            "observed": 20,
        }
    )
    assert status == "PARTIAL"
    assert (
        stratum_observation_status(
            {
                "completeness": "PARTIAL",
                "search_status": "BLOCKED",
                "used_fallback": False,
                "observed": 0,
            }
        )
        == "BLOCKED"
    )


def test_old_keyword_observations_remain_and_are_distinct(session: Session) -> None:
    run_id = _run(session)
    products = ProductRepository(session)
    product = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="OLD-SKU",
        canonical_url="https://example.test/OLD-SKU",
        title="Legacy Gaming Laptop Intel",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        collection_run_id=run_id,
    )
    session.flush()
    # Historical keyword row: no observation_source (pre-migration shape).
    session.add(
        SearchObservation(
            collection_run_id=run_id,
            product_id=product.id,
            observed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            retailer_code="newegg",
            country_code="US",
            keyword="gaming laptop",
            position=4,
            page_number=1,
            retailer_sku="OLD-SKU",
            title="Legacy Gaming Laptop Intel",
            brand="Intel",
            oem="Asus",
            collection_status="COMPLETE",
            observation_source=None,
            stratum=None,
            details=None,
        )
    )
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="intel gaming laptop",
            collection_status=STATUS_COMPLETE,
            pages_collected=1,
            hits=[
                SearchHit(
                    keyword="intel gaming laptop",
                    retailer_code="newegg",
                    country_code="US",
                    position=1,
                    page_number=1,
                    retailer_sku="OLD-SKU",
                    source_url="https://example.test/OLD-SKU",
                    title="Legacy Gaming Laptop Intel",
                    brand="Intel",
                    oem="Asus",
                )
            ],
        ),
        collection_run_id=run_id,
    )
    persist_stratified_catalog_observations(
        session,
        collection_run_id=run_id,
        retailer_code="newegg",
        country_code="US",
        slots=[
            _slot(
                stratum="notebook",
                query="gaming laptop",
                position=1,
                sku="NEW-SKU",
                title="ASUS TUF Gaming Laptop AMD",
                bucket="VALID",
            )
        ],
        strata_reports=[
            {
                "stratum": "notebook",
                "query": "gaming laptop",
                "observed": 1,
                "completeness": "COMPLETE",
                "search_status": "OK",
                "used_fallback": False,
            }
        ],
    )
    session.commit()
    rows = list(session.scalars(select(SearchObservation)))
    assert len(rows) == 3
    historical = [r for r in rows if r.observation_source is None]
    keyword = [r for r in rows if r.observation_source == SOURCE_KEYWORD_SEARCH]
    stratified = [r for r in rows if is_stratified_catalog_observation(r)]
    assert len(historical) == 1
    assert historical[0].keyword == "gaming laptop"
    assert historical[0].position == 4
    assert len(keyword) == 1
    assert keyword[0].keyword == "intel gaming laptop"
    assert len(stratified) == 1
    assert stratified[0].stratum == "notebook"
    assert stratified[0].position == 1
    assert session.scalar(select(func.count()).select_from(SearchObservation)) == 3
    # No product identity invented for the new SKU unless it already existed.
    assert products.get_by_retailer_sku("newegg", "US", "NEW-SKU") is None
