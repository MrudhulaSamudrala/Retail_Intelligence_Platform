"""Stratified-catalog Share of Voice (no live websites, no production collection)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.share_of_voice import (
    SOV_SOURCE_KEYWORD_SEARCH,
    SOV_SOURCE_STRATIFIED_CATALOG,
    SovScope,
    share_of_voice,
)
from collector.search.models import STATUS_COMPLETE, SearchHit, SearchRunResult
from collector.search.persist import (
    persist_search_run,
    persist_stratified_catalog_observations,
)
from collector.universe_config import STRATUM_BUDGETS, strata_for
from database.models import Base, SearchObservation
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
) -> dict:
    return {
        "stratum": stratum,
        "query": query,
        "search_position": position,
        "search_page": 1,
        "universe_slot": position + 1000,
        "sku": sku,
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


def _valid_slots_for_spec(spec, *, brands: tuple[str, ...] = ("Intel", "AMD")) -> list[dict]:
    slots = []
    for index in range(spec.budget):
        brand = brands[index % len(brands)]
        slots.append(
            _slot(
                stratum=spec.name,
                query=spec.query,
                position=index + 1,
                sku=f"{spec.name}-{index + 1}",
                title=f"{brand} gaming {spec.name} {index + 1}",
                bucket="VALID",
                brand=brand,
            )
        )
    return slots


def _full_complete_universe(
    session: Session,
    *,
    retailer: str = "newegg",
    country: str = "US",
    brands: tuple[str, ...] = ("Intel", "AMD"),
) -> None:
    specs = strata_for(retailer)
    slots: list[dict] = []
    reports: list[dict] = []
    for spec in specs:
        slots.extend(_valid_slots_for_spec(spec, brands=brands))
        reports.append(_report(spec, completeness="COMPLETE", used_fallback=False))
    _persist(session, slots, reports, retailer=retailer, country=country)


def test_configured_stratum_budgets_come_from_yaml() -> None:
    specs = {spec.name: spec.budget for spec in strata_for("newegg")}
    assert specs == dict(STRATUM_BUDGETS)
    assert specs == {
        "notebook": 20,
        "desktop": 20,
        "workstation": 20,
        "tablet": 20,
        "gpu": 10,
        "cpu": 10,
    }


def test_stratified_catalog_rows_are_used_by_sov(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "notebook")
    _persist(
        session,
        [
            _slot(
                stratum="notebook",
                query=spec.query,
                position=1,
                sku="NB-1",
                title="Intel Core i7 gaming laptop",
                bucket="VALID",
                brand="Intel",
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
        retailer="newegg",
    )
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.observation_source == SOV_SOURCE_STRATIFIED_CATALOG
    intel = next(m for m in snap.metrics if m.brand == "Intel")
    assert intel.appearances == 1
    assert snap.tracked_appearances == 1


def test_old_keyword_search_rows_are_not_mixed_into_stratified_sov(
    session: Session,
) -> None:
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
                position=1,
                sku="NB-INTEL-1",
                title="Intel Core i9 gaming laptop",
                bucket="VALID",
                brand="Intel",
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
    )
    snap = share_of_voice(session)
    assert snap.observation_source == SOV_SOURCE_STRATIFIED_CATALOG
    intel = next(m for m in snap.metrics if m.brand == "Intel")
    amd = next(m for m in snap.metrics if m.brand == "AMD")
    assert intel.appearances == 1
    assert amd.appearances == 0
    assert snap.tracked_appearances == 1

    historical = share_of_voice(
        session, scope=SovScope(observation_source=SOV_SOURCE_KEYWORD_SEARCH)
    )
    hist_amd = next(m for m in historical.metrics if m.brand == "AMD")
    hist_intel = next(m for m in historical.metrics if m.brand == "Intel")
    assert hist_amd.appearances == 1
    assert hist_intel.appearances == 0


def test_six_strata_use_configured_budgets_for_completeness(session: Session) -> None:
    _full_complete_universe(session)
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.stratum_status == {
        "notebook": "COMPLETE",
        "desktop": "COMPLETE",
        "workstation": "COMPLETE",
        "tablet": "COMPLETE",
        "gpu": "COMPLETE",
        "cpu": "COMPLETE",
    }
    assert snap.total_observations == sum(STRATUM_BUDGETS.values())
    assert snap.collection_basis == "exact"
    by_stratum = {}
    for metric in snap.stratum_metrics:
        by_stratum.setdefault(metric.stratum, {})[metric.brand] = metric.appearances
    for name, budget in STRATUM_BUDGETS.items():
        assert name in by_stratum
        assert sum(by_stratum[name].values()) == budget


def test_native_position_is_preserved(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "workstation")
    _persist(
        session,
        [
            _slot(
                stratum="workstation",
                query=spec.query,
                position=1,
                sku="WS-1",
                title="AMD Ryzen workstation",
                bucket="VALID",
                brand="AMD",
            ),
            _slot(
                stratum="workstation",
                query=spec.query,
                position=7,
                sku="DESK-7",
                title='Electric RGB Gaming Standing Desk 55"',
                bucket="EXCLUDED",
                brand="UNKNOWN",
                oem="UNKNOWN",
            ),
            _slot(
                stratum="workstation",
                query=spec.query,
                position=8,
                sku="WS-1",
                title="AMD Ryzen workstation",
                bucket="DUPLICATE",
                brand="AMD",
            ),
        ],
        [_report(spec, observed=3, completeness="PARTIAL")],
    )
    rows = session.scalars(select(SearchObservation).order_by(SearchObservation.position)).all()
    assert [r.position for r in rows] == [1, 7, 8]
    assert all((r.details or {}).get("universe_slot") != r.position for r in rows)
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg", stratum="workstation"))
    amd = next(m for m in snap.metrics if m.brand == "AMD")
    assert amd.rank_observation_count == 1
    assert amd.average_rank is not None
    assert float(amd.average_rank) == 1.0


def test_excluded_product_consumes_position_not_tracked_brand(session: Session) -> None:
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
            ),
            _slot(
                stratum="desktop",
                query=spec.query,
                position=7,
                sku="DESK-7",
                title="Gaming desk furniture",
                bucket="EXCLUDED",
                brand="Intel",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.total_observations == 2
    assert snap.excluded_observations == 1
    assert snap.tracked_appearances == 1
    intel = next(m for m in snap.metrics if m.brand == "Intel")
    assert intel.appearances == 1


def test_duplicate_sku_does_not_double_count_unique_identity(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "gpu")
    _persist(
        session,
        [
            _slot(
                stratum="gpu",
                query=spec.query,
                position=1,
                sku="GPU-1",
                title="AMD Radeon gaming GPU",
                bucket="VALID",
                brand="AMD",
            ),
            _slot(
                stratum="gpu",
                query=spec.query,
                position=4,
                sku="GPU-1",
                title="AMD Radeon gaming GPU",
                bucket="DUPLICATE",
                brand="AMD",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg", stratum="gpu"))
    assert snap.duplicate_observations == 1
    amd = next(m for m in snap.metrics if m.brand == "AMD")
    # Appearance formula: only the VALID slot counts. Unique SKU identity is 1.
    assert amd.appearances == 1
    assert snap.unique_tracked_skus == 1
    assert snap.tracked_appearances == 1
    rows = session.scalars(select(SearchObservation)).all()
    assert {r.position for r in rows} == {1, 4}
    assert {r.retailer_sku for r in rows} == {"GPU-1"}


def test_unknown_brand_is_not_assigned_to_tracked_brand(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "tablet")
    _persist(
        session,
        [
            _slot(
                stratum="tablet",
                query=spec.query,
                position=1,
                sku="TAB-1",
                title="Android octa-core gaming tablet",
                bucket="VALID",
                brand="UNKNOWN",
            ),
            _slot(
                stratum="tablet",
                query=spec.query,
                position=2,
                sku="TAB-2",
                title="Intel Core Ultra gaming tablet",
                bucket="VALID",
                brand="Intel",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.unknown_appearances == 1
    intel = next(m for m in snap.metrics if m.brand == "Intel")
    assert intel.appearances == 1
    assert snap.tracked_appearances == 1
    assert intel.share_of_voice == Decimal("1.0000")
    for brand in ("AMD", "Qualcomm", "Apple"):
        metric = next(m for m in snap.metrics if m.brand == brand)
        assert metric.appearances == 0


def test_other_brand_is_not_assigned_to_tracked_brand(session: Session) -> None:
    spec = next(s for s in strata_for("newegg") if s.name == "gpu")
    _persist(
        session,
        [
            _slot(
                stratum="gpu",
                query=spec.query,
                position=1,
                sku="GPU-NV",
                title="NVIDIA GeForce RTX 4070",
                bucket="VALID",
                brand="OTHER",
            ),
            _slot(
                stratum="gpu",
                query=spec.query,
                position=2,
                sku="GPU-AMD",
                title="AMD Radeon RX 7800",
                bucket="VALID",
                brand="AMD",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
    )
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.other_appearances == 1
    amd = next(m for m in snap.metrics if m.brand == "AMD")
    assert amd.appearances == 1
    assert snap.tracked_appearances == 1
    assert amd.share_of_voice == Decimal("1.0000")
    for brand in ("Intel", "Qualcomm", "Apple"):
        metric = next(m for m in snap.metrics if m.brand == brand)
        assert metric.appearances == 0


def test_complete_requires_full_configured_budget_for_every_stratum(
    session: Session,
) -> None:
    _full_complete_universe(session)
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.collection_basis == "exact"
    assert snap.complete_searches == 6
    assert snap.partial_searches == 0
    assert all(status == "COMPLETE" for status in snap.stratum_status.values())


def test_missing_positions_produce_partial(session: Session) -> None:
    specs = list(strata_for("newegg"))
    slots: list[dict] = []
    reports: list[dict] = []
    for spec in specs:
        if spec.name == "gpu":
            for index in range(7):
                slots.append(
                    _slot(
                        stratum=spec.name,
                        query=spec.query,
                        position=index + 1,
                        sku=f"gpu-{index + 1}",
                        title=f"AMD Radeon gpu {index + 1}",
                        bucket="VALID",
                        brand="AMD",
                    )
                )
            reports.append(_report(spec, observed=7, completeness="PARTIAL"))
        else:
            slots.extend(_valid_slots_for_spec(spec))
            reports.append(_report(spec, completeness="COMPLETE"))
    _persist(session, slots, reports)
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    assert snap.stratum_status["gpu"] == "PARTIAL"
    assert snap.stratum_status["notebook"] == "COMPLETE"
    assert snap.collection_basis == "observed_partial"
    assert snap.complete_searches == 5
    assert snap.partial_searches == 1


def test_mercadolibre_fallback_cannot_produce_complete_ranked_sov(
    session: Session,
) -> None:
    specs = list(strata_for("mercadolibre"))
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
                    title=f"Intel notebook gamer {index + 1}",
                    bucket="VALID",
                    brand="Intel",
                    used_fallback=True,
                )
            )
        reports.append(
            _report(
                spec,
                completeness="COMPLETE",
                used_fallback=True,
                search_status="BLOCKED",
            )
        )
    _persist(
        session,
        slots,
        reports,
        retailer="mercadolibre",
        country="BR",
    )
    snap = share_of_voice(
        session, scope=SovScope(retailer_code="mercadolibre", country_code="BR")
    )
    assert all(status == "PARTIAL" for status in snap.stratum_status.values())
    assert snap.collection_basis == "observed_partial"
    assert snap.complete_searches == 0
    assert "COMPLETE" not in snap.stratum_status.values()


def test_qualcomm_and_apple_counted_only_with_evidence(session: Session) -> None:
    _full_complete_universe(session, brands=("Intel", "AMD"))
    snap = share_of_voice(session, scope=SovScope(retailer_code="newegg"))
    qcom = next(m for m in snap.metrics if m.brand == "Qualcomm")
    apple = next(m for m in snap.metrics if m.brand == "Apple")
    assert qcom.appearances == 0
    assert apple.appearances == 0
    assert qcom.share_of_voice == Decimal("0")
    assert apple.share_of_voice == Decimal("0")
    tablet = {
        m.brand: m.appearances
        for m in snap.stratum_metrics
        if m.stratum == "tablet"
    }
    assert tablet["Qualcomm"] == 0
    assert tablet["Apple"] == 0

    spec = next(s for s in strata_for("newegg") if s.name == "tablet")
    _persist(
        session,
        [
            _slot(
                stratum="tablet",
                query=spec.query,
                position=3,
                sku="TAB-Q",
                title="Qualcomm Snapdragon X Elite gaming tablet",
                bucket="VALID",
                brand="Qualcomm",
            ),
            _slot(
                stratum="tablet",
                query=spec.query,
                position=4,
                sku="TAB-A",
                title="Apple iPad Pro M4",
                bucket="VALID",
                brand="Apple",
            ),
        ],
        [_report(spec, observed=2, completeness="PARTIAL")],
        observed_at=datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
    )
    later = share_of_voice(
        session, scope=SovScope(retailer_code="newegg", stratum="tablet")
    )
    later_q = next(m for m in later.metrics if m.brand == "Qualcomm")
    later_a = next(m for m in later.metrics if m.brand == "Apple")
    assert later_q.appearances == 1
    assert later_a.appearances == 1


def test_historical_keyword_observations_remain_unchanged(session: Session) -> None:
    persist_search_run(
        session,
        SearchRunResult(
            retailer_code="newegg",
            country_code="US",
            keyword="gaming laptop",
            collection_status=STATUS_COMPLETE,
            pages_collected=1,
            observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            hits=[
                SearchHit(
                    keyword="gaming laptop",
                    retailer_code="newegg",
                    country_code="US",
                    position=3,
                    page_number=1,
                    retailer_sku="HIST-SKU",
                    source_url="https://example.test/hist",
                    title="AMD Ryzen historical laptop",
                    brand="AMD",
                    oem="Asus",
                    is_sponsored=False,
                    evidence_text="AMD Ryzen historical laptop",
                    selector=".item-cell",
                    search_url="https://example.test/search",
                )
            ],
        ),
    )
    session.commit()
    before = session.scalars(select(SearchObservation)).all()
    assert len(before) == 1
    snapshot = {
        "id": before[0].id,
        "brand": before[0].brand,
        "position": before[0].position,
        "keyword": before[0].keyword,
        "observation_source": before[0].observation_source,
        "retailer_sku": before[0].retailer_sku,
        "title": before[0].title,
        "stratum": before[0].stratum,
    }
    spec = next(s for s in strata_for("newegg") if s.name == "cpu")
    _persist(
        session,
        [
            _slot(
                stratum="cpu",
                query=spec.query,
                position=1,
                sku="CPU-1",
                title="Intel Core i9 processor",
                bucket="VALID",
                brand="Intel",
            )
        ],
        [_report(spec, observed=1, completeness="PARTIAL")],
    )
    share_of_voice(session)
    share_of_voice(
        session, scope=SovScope(observation_source=SOV_SOURCE_KEYWORD_SEARCH)
    )
    after = session.scalars(
        select(SearchObservation).where(SearchObservation.retailer_sku == "HIST-SKU")
    ).one()
    assert after.id == snapshot["id"]
    assert after.brand == snapshot["brand"]
    assert after.position == snapshot["position"]
    assert after.keyword == snapshot["keyword"]
    assert after.observation_source == snapshot["observation_source"]
    assert after.retailer_sku == snapshot["retailer_sku"]
    assert after.title == snapshot["title"]
    assert after.stratum == snapshot["stratum"]
    assert after.observation_source == SOV_SOURCE_KEYWORD_SEARCH
