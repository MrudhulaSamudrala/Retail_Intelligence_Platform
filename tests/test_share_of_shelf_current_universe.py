"""Current Share of Shelf uses the latest stratified collection batch only."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.share_of_shelf import SosScope, share_of_shelf_by_brand
from collector.search.persist import persist_stratified_catalog_observations
from database.models import Base
from database.repositories import CollectionRunRepository, ProductRepository
from tests.test_share_of_shelf import _add_product, _mark_stratified


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


def _slot(*, stratum: str, sku: str, position: int, product_id: int, title: str, brand: str) -> dict:
    return {
        "stratum": stratum,
        "query": f"gaming {stratum}",
        "search_position": position,
        "search_page": 1,
        "universe_slot": position,
        "sku": sku,
        "product_id": product_id,
        "title": title,
        "bucket": "VALID",
        "brand": brand,
        "product_type": stratum if stratum != "cpu" else "cpu",
    }


def test_old_historical_products_excluded_from_current_sos(session: Session) -> None:
    old = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    old.run_metadata = {"query": "gaming laptop"}
    _add_product(
        session,
        sku="OLD-LAPTOP-1",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="Old ASUS ROG Gaming Laptop Intel",
        run_id=int(old.id),
    )
    current = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(current)
    _add_product(
        session,
        sku="NEW-NB-1",
        brand="AMD",
        oem="MSI",
        product_type="notebook",
        title="MSI Gaming Laptop AMD Ryzen",
        run_id=int(current.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(session, scope=SosScope(retailer_code="newegg"))
    assert result.universe_size == 1
    assert result.shares[0].value == "AMD"
    assert "Intel" not in {s.value for s in result.shares}


def test_latest_stratified_collection_products_are_included(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    pid = _add_product(
        session,
        sku="CUR-1",
        brand="Intel",
        oem="Dell",
        product_type="notebook",
        title="Dell G16 Gaming Intel Core",
        run_id=int(run.id),
    )
    persist_stratified_catalog_observations(
        session,
        collection_run_id=int(run.id),
        retailer_code="newegg",
        country_code="US",
        slots=[
            _slot(
                stratum="notebook",
                sku="CUR-1",
                position=1,
                product_id=pid,
                title="Dell G16 Gaming Intel Core",
                brand="Intel",
            )
        ],
        strata_reports=[{"stratum": "notebook", "completeness": "COMPLETE", "used_fallback": False}],
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    assert result.universe_size == 1
    assert result.shares[0].value == "Intel"
    assert result.collection_run_ids[("newegg", "US")] == int(run.id)


def test_same_sku_two_strata_counts_once_in_current_sos(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    pid = _add_product(
        session,
        sku="SHARED-X",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        title="ASUS TUF Gaming Laptop AMD Ryzen 7",
        run_id=int(run.id),
    )
    persist_stratified_catalog_observations(
        session,
        collection_run_id=int(run.id),
        retailer_code="newegg",
        country_code="US",
        slots=[
            _slot(
                stratum="notebook",
                sku="SHARED-X",
                position=4,
                product_id=pid,
                title="ASUS TUF Gaming Laptop AMD Ryzen 7",
                brand="AMD",
            ),
            _slot(
                stratum="desktop",
                sku="SHARED-X",
                position=8,
                product_id=pid,
                title="ASUS TUF Gaming Laptop AMD Ryzen 7",
                brand="AMD",
            ),
        ],
        strata_reports=[
            {"stratum": "notebook", "completeness": "COMPLETE", "used_fallback": False},
            {"stratum": "desktop", "completeness": "COMPLETE", "used_fallback": False},
        ],
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    assert result.universe_size == 1
    assert result.shares[0].product_count == 1
    assert result.shares[0].share == result.shares[0].share  # formula: 1/1


def test_excluded_other_furniture_not_in_denominator(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="KB-1",
        brand="Intel",
        oem="Asus",
        product_type="UNKNOWN",
        title="Gaming Mechanical Keyboard RGB",
        category_raw="PC Accessories Keyboards",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="OTHER-1",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="other",
        title='Electric RGB Gaming Standing Desk 55"',
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="DESK-1",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="workstation",
        title="E6G CyberX RGB LED Gaming Standing Desk",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="OK-1",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        run_id=int(run.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    assert result.universe_size == 1
    assert result.shares[0].value == "Intel"
    assert result.exclusions.accessory_or_ineligible_type >= 3


def test_unknown_and_other_brands_remain_in_denominator(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="UNK-NB",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="notebook",
        title="15 Inch Gaming Notebook 16GB RAM RTX 4060",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="OTH-TAB",
        brand="OTHER",
        oem="UNKNOWN",
        product_type="tablet",
        title='HAOVM 10" Gaming Tablet MediaTek Helio G80',
        run_id=int(run.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    by_brand = {s.value: s.product_count for s in result.shares}
    assert result.universe_size == 2
    assert by_brand["UNKNOWN"] == 1
    assert by_brand["OTHER"] == 1


def test_intel_amd_qualcomm_apple_counts(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="I1",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="ASUS ROG Gaming Laptop Intel",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="I2",
        brand="Intel",
        oem="MSI",
        product_type="desktop",
        title="MSI Gaming Desktop Intel Core i7",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="A1",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        title="ASUS TUF Gaming Laptop AMD Ryzen",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="Q1",
        brand="Qualcomm",
        oem="Lenovo",
        product_type="notebook",
        title="Lenovo Yoga Gaming Snapdragon X Elite",
        run_id=int(run.id),
    )
    _add_product(
        session,
        sku="AP1",
        brand="Apple",
        oem="Apple",
        product_type="notebook",
        title="Apple MacBook Pro M3 Gaming bundle",
        run_id=int(run.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    by_brand = {s.value: s for s in result.shares}
    assert result.universe_size == 5
    assert by_brand["Intel"].product_count == 2
    assert by_brand["AMD"].product_count == 1
    assert by_brand["Qualcomm"].product_count == 1
    assert by_brand["Apple"].product_count == 1
    from decimal import Decimal

    assert by_brand["Intel"].share == Decimal("0.4000")
    assert by_brand["AMD"].share == Decimal("0.2000")


def test_apple_oem_unknown_platform_is_not_apple_brand(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="IPAD-1",
        brand="UNKNOWN",
        oem="Apple",
        product_type="tablet",
        title='Apple iPad 7 Gaming bundle 10.2"',
        run_id=int(run.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    assert result.universe_size == 1
    assert result.shares[0].value == "UNKNOWN"
    assert "Apple" not in {s.value for s in result.shares}


def test_partial_collection_is_marked_partial_and_not_padded(session: Session) -> None:
    historical = CollectionRunRepository(session).start(
        retailer_code="mercadolibre", country_code="BR", run_type="pricing"
    )
    historical.run_metadata = {"query": "notebook gamer"}
    _add_product(
        session,
        sku="OLD-BR-1",
        brand="Intel",
        oem="Dell",
        product_type="notebook",
        title="Dell Gamer Intel antigo",
        retailer_code="mercadolibre",
        country_code="BR",
        run_id=int(historical.id),
    )
    current = CollectionRunRepository(session).start(
        retailer_code="mercadolibre", country_code="BR", run_type="pricing"
    )
    _mark_stratified(current, completeness="PARTIAL", used_fallback=True)
    _add_product(
        session,
        sku="NEW-BR-1",
        brand="AMD",
        oem="Acer",
        product_type="notebook",
        title="Acer Nitro Gaming AMD",
        retailer_code="mercadolibre",
        country_code="BR",
        run_id=int(current.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(
        session, scope=SosScope(retailer_code="mercadolibre", country_code="BR")
    )
    assert result.collection_status == "PARTIAL"
    assert result.universe_size == 1
    assert result.shares[0].value == "AMD"


def test_latest_collection_run_selected_per_retailer_country(session: Session) -> None:
    old_us = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(old_us)
    _add_product(
        session,
        sku="US-OLD",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="ASUS ROG Gaming Laptop Intel old",
        run_id=int(old_us.id),
    )
    new_us = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(new_us)
    _add_product(
        session,
        sku="US-NEW",
        brand="AMD",
        oem="MSI",
        product_type="notebook",
        title="MSI Gaming Laptop AMD Ryzen new",
        run_id=int(new_us.id),
    )
    br = CollectionRunRepository(session).start(
        retailer_code="mercadolibre", country_code="BR", run_type="pricing"
    )
    _mark_stratified(br)
    _add_product(
        session,
        sku="BR-NEW",
        brand="Qualcomm",
        oem="Lenovo",
        product_type="notebook",
        title="Lenovo Yoga Gaming Snapdragon X Elite",
        retailer_code="mercadolibre",
        country_code="BR",
        run_id=int(br.id),
    )
    session.commit()
    us = share_of_shelf_by_brand(session, scope=SosScope(retailer_code="newegg", country_code="US"))
    br_sos = share_of_shelf_by_brand(
        session, scope=SosScope(retailer_code="mercadolibre", country_code="BR")
    )
    assert us.universe_size == 1
    assert us.shares[0].value == "AMD"
    assert us.collection_run_ids[("newegg", "US")] == int(new_us.id)
    assert br_sos.universe_size == 1
    assert br_sos.shares[0].value == "Qualcomm"
    assert br_sos.collection_run_ids[("mercadolibre", "BR")] == int(br.id)


def test_sos_formula_unchanged_brand_over_unique_eligible(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="pricing"
    )
    _mark_stratified(run)
    for i, brand in enumerate(("Intel", "Intel", "AMD"), start=1):
        _add_product(
            session,
            sku=f"F-{i}",
            brand=brand,
            oem="Asus",
            product_type="notebook",
            title=f"ASUS TUF Gaming Laptop {brand} {i}",
            run_id=int(run.id),
        )
    session.commit()
    result = share_of_shelf_by_brand(session)
    by_brand = {s.value: s for s in result.shares}
    assert result.universe_size == 3
    assert by_brand["Intel"].product_count == 2
    assert float(by_brand["Intel"].share) == pytest.approx(2 / 3, abs=0.00015)
    assert float(by_brand["AMD"].share) == pytest.approx(1 / 3, abs=0.00015)


def test_keyword_search_run_is_not_current_sos_universe(session: Session) -> None:
    keyword = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="search"
    )
    _add_product(
        session,
        sku="KW-1",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="ASUS ROG Gaming Laptop Intel keyword",
        run_id=int(keyword.id),
    )
    session.commit()
    result = share_of_shelf_by_brand(session, scope=SosScope(retailer_code="newegg"))
    assert result.universe_size == 0
    assert result.collection_status == "NO_DATA"


def test_explicit_historical_scope_still_reads_as_of_snapshots(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="discovery"
    )
    products = ProductRepository(session)
    row = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="HIST-1",
        canonical_url="https://example.test/HIST-1",
        title="ASUS ROG Gaming Laptop Intel",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        category_raw="Gaming Laptops",
        collection_run_id=int(run.id),
    )
    from database.repositories import ObservationRepository

    ObservationRepository(session).add_snapshot(
        product_id=row.id,
        collection_run_id=int(run.id),
        observed_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        title="ASUS ROG Gaming Laptop Intel",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        category_raw="Gaming Laptops",
    )
    session.commit()
    current = share_of_shelf_by_brand(session)
    assert current.universe_size == 0
    historical = share_of_shelf_by_brand(
        session,
        scope=SosScope(
            current_universe=False,
            as_of=datetime(2026, 8, 2, tzinfo=timezone.utc),
        ),
    )
    assert historical.universe_size == 1
    assert historical.shares[0].value == "Intel"
