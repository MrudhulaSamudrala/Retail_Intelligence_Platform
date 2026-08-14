"""Stratified-catalog product visibility (no live websites, no production collection)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.product_visibility.models import (
    VISIBILITY_SOURCE_KEYWORD_SEARCH,
    VISIBILITY_SOURCE_STRATIFIED_CATALOG,
    VisibilityScope,
)
from analytics.product_visibility.queries import list_product_visibility
from collector.search.models import STATUS_COMPLETE, SearchHit, SearchRunResult
from collector.search.persist import persist_search_run, persist_stratified_catalog_observations
from collector.universe_config import STRATUM_BUDGETS, strata_for
from database.models import Base, Product, SearchObservation
from database.repositories import CollectionRunRepository


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):  # type: ignore[untyped-def]
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


def _start_run(session: Session, retailer: str = "newegg", country: str = "US") -> int:
    row = CollectionRunRepository(session).start(
        retailer_code=retailer, country_code=country, run_type="catalog"
    )
    session.flush()
    return int(row.id)


def _add_product(
    session: Session,
    *,
    sku: str,
    title: str,
    brand: str,
    oem: str = "Asus",
    product_type: str = "notebook",
    retailer: str = "newegg",
    country: str = "US",
) -> Product:
    product = Product(
        retailer_code=retailer,
        country_code=country,
        retailer_sku=sku,
        canonical_url=f"https://example.test/{retailer}/{sku}",
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        is_active=True,
        first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(product)
    session.flush()
    return product


def _slot(
    *,
    stratum: str,
    query: str,
    position: int,
    sku: str | None,
    title: str,
    bucket: str,
    brand: str | None = "AMD",
    oem: str | None = "Asus",
    used_fallback: bool = False,
    product_type: str | None = None,
    product_id: int | None = None,
) -> dict:
    return {
        "stratum": stratum,
        "query": query,
        "search_position": position,
        "search_page": 1,
        "universe_slot": position + 1000,
        "sku": sku,
        "product_id": product_id,
        "title": title,
        "bucket": bucket,
        "extraction_status": "EXTRACTED" if bucket in {"VALID", "EXCLUDED"} else bucket,
        "brand": brand,
        "oem": oem,
        "url": f"https://example.test/{sku or position}",
        "is_sponsored": False,
        "used_fallback": used_fallback,
        "gaming": bucket == "VALID",
        "product_type": product_type or stratum,
    }


def _report(
    spec,
    *,
    observed: int | None = None,
    completeness: str = "COMPLETE",
    used_fallback: bool = False,
    search_status: str = "OK",
) -> dict:
    count = spec.budget if observed is None else observed
    return {
        "stratum": spec.name,
        "query": spec.query,
        "observed": count,
        "requested": spec.budget,
        "completeness": completeness,
        "search_status": search_status,
        "used_fallback": used_fallback,
    }


def _persist(
    session: Session,
    slots: list[dict],
    reports: list[dict],
    *,
    retailer: str = "newegg",
    country: str = "US",
    observed_at: datetime | None = None,
) -> None:
    persist_stratified_catalog_observations(
        session,
        collection_run_id=_start_run(session, retailer, country),
        retailer_code=retailer,
        country_code=country,
        slots=slots,
        strata_reports=reports,
        observed_at=observed_at or datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
    )
    session.commit()


def test_visibility_reads_stratified_catalog(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "notebook")
    product = _add_product(
        session, sku="NB-1", title="Intel Core i7 gaming laptop", brand="Intel"
    )
    session.commit()
    _persist(
        session,
        [
            _slot(
                stratum="notebook",
                query=spec.query,
                position=4,
                sku="NB-1",
                title=product.title or "",
                bucket="VALID",
                brand="Intel",
                product_id=product.id,
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
    )
    rows = list_product_visibility(session, VisibilityScope(retailer_code="newegg"))
    assert rows
    assert rows[0].observation_source == VISIBILITY_SOURCE_STRATIFIED_CATALOG
    assert rows[0].product_id == product.id
    assert rows[0].appearances == 1
    assert rows[0].average_rank == Decimal("4.00")
    assert rows[0].positions_by_stratum == (("notebook", 4),)


def test_old_keyword_observations_ignored_for_visibility_snapshot(session: Session) -> None:
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
                SearchHit(
                    keyword="gaming laptop",
                    retailer_code="newegg",
                    country_code="US",
                    position=1,
                    page_number=1,
                    retailer_sku="KW-AMD-1",
                    source_url="https://example.test/kw",
                    title="AMD Ryzen gaming laptop",
                    brand="AMD",
                    oem="Asus",
                    is_sponsored=False,
                    evidence_text="AMD Ryzen gaming laptop",
                    selector=".item-cell",
                    search_url="https://example.test/search",
                )
            ],
        ),
    )
    session.commit()
    spec = next(s for s in strata_for("newegg") if s.name == "notebook")
    _persist(
        session,
        [
            _slot(
                stratum="notebook",
                query=spec.query,
                position=2,
                sku="NB-INTEL",
                title="Intel Core i9 gaming laptop",
                bucket="VALID",
                brand="Intel",
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
    )
    rows = list_product_visibility(session, VisibilityScope(retailer_code="newegg"))
    skus = {r.retailer_sku for r in rows}
    assert "NB-INTEL" in skus
    assert "KW-AMD-1" not in skus
    historical = list_product_visibility(
        session,
        VisibilityScope(
            retailer_code="newegg",
            observation_source=VISIBILITY_SOURCE_KEYWORD_SEARCH,
        ),
    )
    assert {r.retailer_sku for r in historical} == {"KW-AMD-1"}


def test_native_search_position_is_preserved(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "workstation")
    product = _add_product(
        session,
        sku="WS-1",
        title="AMD Ryzen workstation",
        brand="AMD",
        product_type="workstation",
    )
    session.commit()
    _persist(
        session,
        [
            _slot(
                stratum="workstation",
                query=spec.query,
                position=1,
                sku="WS-1",
                title=product.title or "",
                bucket="VALID",
                brand="AMD",
                product_id=product.id,
                product_type="workstation",
            ),
            _slot(
                stratum="workstation",
                query=spec.query,
                position=7,
                sku="DESK-7",
                title='Electric RGB Gaming Standing Desk 55"',
                bucket="EXCLUDED",
                brand="UNKNOWN",
                product_type="other",
            ),
            _slot(
                stratum="workstation",
                query=spec.query,
                position=8,
                sku="WS-1",
                title=product.title or "",
                bucket="DUPLICATE",
                brand="AMD",
                product_id=product.id,
                product_type="workstation",
            ),
        ],
        [_report(spec, observed=3, completeness="PARTIAL")],
    )
    stored = session.scalars(select(SearchObservation).order_by(SearchObservation.position)).all()
    assert [r.position for r in stored] == [1, 7, 8]
    assert all((r.details or {}).get("universe_slot") != r.position for r in stored)
    rows = list_product_visibility(session, VisibilityScope(retailer_code="newegg"))
    scored = next(r for r in rows if r.retailer_sku == "WS-1")
    assert scored.appearances == 2
    assert scored.average_rank == Decimal("4.50")
    positions = [pos for _, pos in scored.positions_by_stratum]
    assert 7 not in positions
    assert positions == [1, 8]


def test_excluded_products_do_not_receive_visibility_scores(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "desktop")
    _persist(
        session,
        [
            _slot(
                stratum="desktop",
                query=spec.query,
                position=1,
                sku="DT-1",
                title="Intel Core i7 gaming desktop",
                bucket="VALID",
                brand="Intel",
                product_type="desktop",
            ),
            _slot(
                stratum="desktop",
                query=spec.query,
                position=7,
                sku="DESK-7",
                title="Gaming desk furniture",
                bucket="EXCLUDED",
                brand="UNKNOWN",
                product_type="other",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    rows = list_product_visibility(
        session, VisibilityScope(retailer_code="newegg", top_n=100)
    )
    skus = {r.retailer_sku for r in rows}
    assert "DT-1" in skus
    assert "DESK-7" not in skus
    desk = session.scalars(
        select(SearchObservation).where(SearchObservation.retailer_sku == "DESK-7")
    ).one()
    assert desk.position == 7


def test_duplicate_sku_observations_remain_traceable(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "gpu")
    product = _add_product(
        session,
        sku="GPU-1",
        title="AMD Radeon gaming GPU",
        brand="AMD",
        product_type="gpu",
    )
    session.commit()
    _persist(
        session,
        [
            _slot(
                stratum="gpu",
                query=spec.query,
                position=1,
                sku="GPU-1",
                title=product.title or "",
                bucket="VALID",
                brand="AMD",
                product_id=product.id,
                product_type="gpu",
            ),
            _slot(
                stratum="gpu",
                query=spec.query,
                position=4,
                sku="GPU-1",
                title=product.title or "",
                bucket="DUPLICATE",
                brand="AMD",
                product_id=product.id,
                product_type="gpu",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    rows = [
        r
        for r in list_product_visibility(session, VisibilityScope(retailer_code="newegg"))
        if r.product_id == product.id
    ]
    assert len(rows) == 1
    # Existing formula: repeated SERP slots of the same SKU are appearances, not new identities.
    assert rows[0].appearances == 2
    assert rows[0].positions_by_stratum == (("gpu", 1), ("gpu", 4))
    assert rows[0].average_rank == Decimal("2.50")


def test_same_sku_across_strata_is_one_product_identity(session: Session) -> None:
    notebook = next(s for s in strata_for("newegg") if s.name == "notebook")
    desktop = next(s for s in strata_for("newegg") if s.name == "desktop")
    product = _add_product(
        session, sku="X-1", title="Intel gaming PC convertible", brand="Intel"
    )
    session.commit()
    _persist(
        session,
        [
            _slot(
                stratum="notebook",
                query=notebook.query,
                position=4,
                sku="X-1",
                title=product.title or "",
                bucket="VALID",
                brand="Intel",
                product_id=product.id,
            ),
            _slot(
                stratum="desktop",
                query=desktop.query,
                position=8,
                sku="X-1",
                title=product.title or "",
                bucket="VALID",
                brand="Intel",
                product_id=product.id,
                product_type="desktop",
            ),
        ],
        [
            _report(notebook, observed=1, completeness="PARTIAL"),
            _report(desktop, observed=1, completeness="PARTIAL"),
        ],
    )
    rows = [
        r
        for r in list_product_visibility(session, VisibilityScope(retailer_code="newegg"))
        if r.product_id == product.id
    ]
    assert len(rows) == 1
    assert rows[0].appearances == 2
    assert rows[0].strata == ("desktop", "notebook")
    assert rows[0].positions_by_stratum == (("desktop", 8), ("notebook", 4))
    products = session.scalars(select(Product).where(Product.retailer_sku == "X-1")).all()
    assert len(products) == 1


def test_six_stratum_budgets_are_respected(session: Session) -> None:
    specs = list(strata_for("newegg"))
    assert {s.name: s.budget for s in specs} == dict(STRATUM_BUDGETS)
    slots: list[dict] = []
    reports: list[dict] = []
    for spec in specs:
        for index in range(spec.budget):
            slots.append(
                _slot(
                    stratum=spec.name,
                    query=spec.query,
                    position=index + 1,
                    sku=f"{spec.name}-{index + 1}",
                    title=f"AMD gaming {spec.name} {index + 1}",
                    bucket="VALID",
                    brand="AMD",
                    product_type=spec.name,
                )
            )
        reports.append(_report(spec, completeness="COMPLETE"))
    _persist(session, slots, reports)
    gpu_rows = list_product_visibility(
        session, VisibilityScope(retailer_code="newegg", stratum="gpu", top_n=100)
    )
    notebook_rows = list_product_visibility(
        session, VisibilityScope(retailer_code="newegg", stratum="notebook", top_n=100)
    )
    assert len(gpu_rows) == STRATUM_BUDGETS["gpu"]
    assert len(notebook_rows) == STRATUM_BUDGETS["notebook"]
    assert all(r.requested_budgets == (("gpu", 10),) for r in gpu_rows)
    assert all(r.collection_status == "COMPLETE" for r in gpu_rows)
    assert all(r.collection_status == "COMPLETE" for r in notebook_rows)
    assert max(pos for row in gpu_rows for _, pos in row.positions_by_stratum) == 10


def test_partial_strata_are_marked_partial(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "gpu")
    slots = [
        _slot(
            stratum="gpu",
            query=spec.query,
            position=index + 1,
            sku=f"gpu-{index + 1}",
            title=f"AMD Radeon {index + 1}",
            bucket="VALID",
            brand="AMD",
            product_type="gpu",
        )
        for index in range(7)
    ]
    _persist(session, slots, [_report(spec, observed=7, completeness="PARTIAL")])
    rows = list_product_visibility(
        session, VisibilityScope(retailer_code="newegg", stratum="gpu")
    )
    assert rows
    assert all(r.collection_status == "PARTIAL" for r in rows)
    assert all(r.stratum_status == (("gpu", "PARTIAL"),) for r in rows)
    assert all(r.requested_budgets == (("gpu", 10),) for r in rows)


def test_complete_strata_are_marked_complete(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "cpu")
    slots = [
        _slot(
            stratum="cpu",
            query=spec.query,
            position=index + 1,
            sku=f"cpu-{index + 1}",
            title=f"AMD Ryzen processor {index + 1}",
            bucket="VALID",
            brand="AMD",
            product_type="cpu",
        )
        for index in range(spec.budget)
    ]
    _persist(session, slots, [_report(spec, completeness="COMPLETE")])
    rows = list_product_visibility(
        session, VisibilityScope(retailer_code="newegg", stratum="cpu")
    )
    assert len(rows) == 10
    assert all(r.collection_status == "COMPLETE" for r in rows)
    assert all(r.stratum_status == (("cpu", "COMPLETE"),) for r in rows)
    assert all(r.requested_budgets == (("cpu", 10),) for r in rows)


def test_mercadolibre_fallback_cannot_become_complete_ranked_visibility(
    session: Session,
) -> None:
    spec = next(s for s in strata_for("mercadolibre") if s.name == "notebook")
    slots = [
        _slot(
            stratum="notebook",
            query=spec.query,
            position=index + 1,
            sku=f"ml-{index + 1}",
            title=f"Intel notebook gamer {index + 1}",
            bucket="VALID",
            brand="Intel",
            used_fallback=True,
        )
        for index in range(spec.budget)
    ]
    _persist(
        session,
        slots,
        [
            _report(
                spec,
                completeness="COMPLETE",
                used_fallback=True,
                search_status="BLOCKED",
            )
        ],
        retailer="mercadolibre",
        country="BR",
    )
    rows = list_product_visibility(
        session,
        VisibilityScope(retailer_code="mercadolibre", country_code="BR"),
    )
    assert rows
    assert all(r.collection_status == "PARTIAL" for r in rows)
    assert all(("notebook", "PARTIAL") in r.stratum_status for r in rows)


def test_unknown_and_other_brands_remain_classified(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "tablet")
    unknown = _add_product(
        session,
        sku="TAB-U",
        title="Android octa-core gaming tablet",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="tablet",
    )
    other = _add_product(
        session,
        sku="TAB-O",
        title="NVIDIA Shield tablet",
        brand="OTHER",
        oem="NVIDIA",
        product_type="tablet",
    )
    session.commit()
    _persist(
        session,
        [
            _slot(
                stratum="tablet",
                query=spec.query,
                position=1,
                sku="TAB-U",
                title=unknown.title or "",
                bucket="VALID",
                brand="UNKNOWN",
                oem="UNKNOWN",
                product_id=unknown.id,
                product_type="tablet",
            ),
            _slot(
                stratum="tablet",
                query=spec.query,
                position=2,
                sku="TAB-O",
                title=other.title or "",
                bucket="VALID",
                brand="OTHER",
                oem="NVIDIA",
                product_id=other.id,
                product_type="tablet",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    rows = {
        r.retailer_sku: r
        for r in list_product_visibility(
            session, VisibilityScope(retailer_code="newegg", top_n=100)
        )
    }
    assert rows["TAB-U"].brand == "UNKNOWN"
    assert rows["TAB-O"].brand == "OTHER"
    assert rows["TAB-U"].oem == "UNKNOWN"
    assert rows["TAB-O"].oem == "NVIDIA"


def test_historical_observations_do_not_replace_latest_stratified_snapshot(
    session: Session,
) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "notebook")
    product = _add_product(
        session, sku="NB-HIST", title="Intel historical then latest", brand="Intel"
    )
    session.commit()
    _persist(
        session,
        [
            _slot(
                stratum="notebook",
                query=spec.query,
                position=9,
                sku="NB-HIST",
                title=product.title or "",
                bucket="VALID",
                brand="Intel",
                product_id=product.id,
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    _persist(
        session,
        [
            _slot(
                stratum="notebook",
                query=spec.query,
                position=2,
                sku="NB-HIST",
                title=product.title or "",
                bucket="VALID",
                brand="Intel",
                product_id=product.id,
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    stored = session.scalars(
        select(SearchObservation).where(SearchObservation.retailer_sku == "NB-HIST")
    ).all()
    assert len(stored) == 2
    rows = list_product_visibility(session, VisibilityScope(retailer_code="newegg"))
    scored = next(r for r in rows if r.product_id == product.id)
    assert scored.appearances == 1
    assert scored.average_rank == Decimal("2.00")
    assert scored.positions_by_stratum == (("notebook", 2),)
