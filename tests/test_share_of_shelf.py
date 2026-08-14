"""Share of Shelf tests with known product counts and expected percentages.

Uses in-memory SQLite and synthetic fixtures only (not real retailer data).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.share_of_shelf import (
    INCLUSION_RULES_ID,
    SosScope,
    is_accessory_excluded,
    is_gaming_eligible,
    share_of_shelf_by_brand,
    share_of_shelf_by_oem,
    share_of_shelf_trends,
)
from analytics.share_of_shelf.universe import build_eligible_universe, load_sos_universe_config
from database.models import Base, CollectionRun
from database.repositories import (
    CollectionRunRepository,
    ObservationRepository,
    ProductRepository,
)

_STRATA = ("notebook", "desktop", "workstation", "tablet", "gpu", "cpu")


def _mark_stratified(
    run: CollectionRun,
    *,
    completeness: str = "COMPLETE",
    used_fallback: bool = False,
) -> None:
    run.status = "completed" if completeness == "COMPLETE" else "partial"
    run.run_metadata = {
        "universe": {
            "completeness": completeness,
            "used_fallback": used_fallback,
            "strata": [
                {
                    "stratum": name,
                    "completeness": completeness,
                    "used_fallback": used_fallback,
                }
                for name in _STRATA
            ],
        }
    }


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


def _add_product(
    session: Session,
    *,
    sku: str,
    brand: str | None,
    oem: str | None,
    product_type: str | None,
    title: str,
    category_raw: str = "Gaming Laptops",
    retailer_code: str = "newegg",
    country_code: str = "US",
    run_id: int,
) -> int:
    products = ProductRepository(session)
    row = products.upsert_identity(
        retailer_code=retailer_code,
        country_code=country_code,
        retailer_sku=sku,
        canonical_url=f"https://example.test/{sku}",
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        category_raw=category_raw,
        collection_run_id=run_id,
    )
    if not product_type or not title:
        session.flush()
        return row.id
    ObservationRepository(session).add_snapshot(
        product_id=row.id,
        collection_run_id=run_id,
        observed_at=datetime.now(timezone.utc),
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        category_raw=category_raw,
    )
    session.flush()
    return row.id


def _seed_known_universe(session: Session) -> dict[str, int]:
    """
    Controlled shelf:

    Eligible gaming (denominator = 10):
      Intel + Asus notebook     x3
      AMD + MSI notebook        x2
      AMD + Asus desktop        x1
      Qualcomm + Lenovo notebook x1
      Apple + Apple notebook    x2   (Brand=Apple, OEM=Apple — count once)
      Intel + Dell notebook OOS title gaming x1

    Excluded from denominator:
      Accessory keyboard (excluded category / wrong type)
      Non-gaming office notebook (no gaming signals)
      Duplicate SKU upsert (same identity — still one product)
    """
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="discovery"
    )
    _mark_stratified(run)
    ids: dict[str, int] = {}

    # 3 Intel / Asus gaming notebooks
    for i in range(1, 4):
        ids[f"intel-{i}"] = _add_product(
            session,
            sku=f"INTEL-NB-{i}",
            brand="Intel",
            oem="Asus",
            product_type="notebook",
            title=f"ASUS ROG Gaming Laptop Intel {i}",
            run_id=run.id,
        )

    # 2 AMD / MSI gaming notebooks
    for i in range(1, 3):
        ids[f"amd-nb-{i}"] = _add_product(
            session,
            sku=f"AMD-NB-{i}",
            brand="AMD",
            oem="MSI",
            product_type="notebook",
            title=f"MSI Gaming Laptop AMD Ryzen {i}",
            run_id=run.id,
        )

    # 1 AMD / Asus gaming desktop
    ids["amd-dt-1"] = _add_product(
        session,
        sku="AMD-DT-1",
        brand="AMD",
        oem="Asus",
        product_type="desktop",
        title="ASUS TUF Gaming Desktop AMD",
        category_raw="Gaming Desktops",
        run_id=run.id,
    )

    # 1 Qualcomm
    ids["qcom-1"] = _add_product(
        session,
        sku="QCOM-NB-1",
        brand="Qualcomm",
        oem="Lenovo",
        product_type="notebook",
        title="Lenovo Gaming Snapdragon X Elite",
        run_id=run.id,
    )

    # 2 Apple (Brand=Apple AND OEM=Apple) — must count once each for brand SoS
    for i in range(1, 3):
        ids[f"apple-{i}"] = _add_product(
            session,
            sku=f"APPLE-NB-{i}",
            brand="Apple",
            oem="Apple",
            product_type="notebook",
            title=f"Apple MacBook Pro M3 Gaming bundle {i}",
            category_raw="Gaming Laptops",
            run_id=run.id,
        )

    # 1 more Intel
    ids["intel-4"] = _add_product(
        session,
        sku="INTEL-NB-4",
        brand="Intel",
        oem="Dell",
        product_type="notebook",
        title="Dell G16 Gaming Intel Core",
        run_id=run.id,
    )

    # EXCLUDED: accessory
    ids["acc-1"] = _add_product(
        session,
        sku="ACC-KB-1",
        brand="Intel",
        oem="Asus",
        product_type="UNKNOWN",
        title="Gaming Mechanical Keyboard RGB",
        category_raw="PC Accessories Keyboards",
        run_id=run.id,
    )

    # EXCLUDED: non-gaming
    ids["office-1"] = _add_product(
        session,
        sku="OFFICE-NB-1",
        brand="Intel",
        oem="HP",
        product_type="notebook",
        title="HP Business Notebook 14",
        category_raw="Business Laptops",
        run_id=run.id,
    )

    # Upsert same SKU again — still one product in universe
    _add_product(
        session,
        sku="INTEL-NB-1",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="ASUS ROG Gaming Laptop Intel 1 updated",
        run_id=run.id,
    )

    CollectionRunRepository(session).complete(run, items_collected=12)
    session.commit()
    return ids


def test_inclusion_helpers_exclude_accessories_and_non_gaming() -> None:
    cfg = load_sos_universe_config()
    assert is_accessory_excluded(
        product_type="UNKNOWN",
        category_raw="Keyboards accessories",
        config=cfg,
    )
    assert is_accessory_excluded(
        product_type="monitor", category_raw="Monitors", config=cfg
    )
    assert not is_accessory_excluded(
        product_type="notebook", category_raw="Gaming Laptops", config=cfg
    )
    assert is_gaming_eligible(
        title="ASUS ROG Strix", category_raw="Laptops", config=cfg
    )
    assert not is_gaming_eligible(
        title="HP Business Notebook", category_raw="Business Laptops", config=cfg
    )


def test_brand_sos_known_counts_and_percentages(session: Session) -> None:
    _seed_known_universe(session)
    result = share_of_shelf_by_brand(session)

    assert result.inclusion_rules_id == INCLUSION_RULES_ID
    # Denominator: 3 Intel Asus + 2 AMD MSI + 1 AMD Asus DT + 1 Qualcomm
    #              + 2 Apple + 1 Intel Dell = 10
    assert result.universe_size == 10

    by_brand = {s.value: s for s in result.shares}
    # Intel: 4 / 10 = 40%
    assert by_brand["Intel"].product_count == 4
    assert by_brand["Intel"].share == Decimal("0.4000")
    # AMD: 3 / 10 = 30%
    assert by_brand["AMD"].product_count == 3
    assert by_brand["AMD"].share == Decimal("0.3000")
    # Apple: 2 / 10 = 20%  (NOT 4 — Brand+OEM must not double-count)
    assert by_brand["Apple"].product_count == 2
    assert by_brand["Apple"].share == Decimal("0.2000")
    # Qualcomm: 1 / 10 = 10%
    assert by_brand["Qualcomm"].product_count == 1
    assert by_brand["Qualcomm"].share == Decimal("0.1000")

    # Shares sum to 1.0 over attributed brands in this fixture
    total = sum(s.share for s in result.shares)
    assert total == Decimal("1.0000")

    # Accessories / non-gaming excluded from denominator
    assert result.exclusions.accessory_or_ineligible_type >= 1
    assert result.exclusions.non_gaming >= 1


def test_apple_brand_oem_not_double_counted(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="discovery"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="APPLE-ONLY-1",
        brand="Apple",
        oem="Apple",
        product_type="notebook",
        title="MacBook Air M2 Gaming edition",
        run_id=run.id,
    )
    _add_product(
        session,
        sku="INTEL-ONLY-1",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        title="ASUS TUF Gaming Intel",
        run_id=run.id,
    )
    session.commit()

    brand = share_of_shelf_by_brand(session)
    assert brand.universe_size == 2
    apple = next(s for s in brand.shares if s.value == "Apple")
    assert apple.product_count == 1
    assert apple.share == Decimal("0.5000")

    # OEM drilldown is separate: Apple OEM also 1/2, not added to brand
    oem = share_of_shelf_by_oem(session)
    apple_oem = next(s for s in oem.shares if s.value == "Apple")
    assert apple_oem.product_count == 1
    assert apple_oem.share == Decimal("0.5000")


def test_retailer_country_product_type_and_oem_filters(session: Session) -> None:
    _seed_known_universe(session)
    # Add a BR listing so country filter is meaningful
    run = CollectionRunRepository(session).start(
        retailer_code="mercadolibre", country_code="BR", run_type="discovery"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="BR-AMD-1",
        brand="AMD",
        oem="Acer",
        product_type="notebook",
        title="Acer Nitro Gaming AMD",
        retailer_code="mercadolibre",
        country_code="BR",
        run_id=run.id,
    )
    session.commit()

    us = share_of_shelf_by_brand(session, scope=SosScope(country_code="US"))
    assert us.universe_size == 10

    br = share_of_shelf_by_brand(session, scope=SosScope(country_code="BR"))
    assert br.universe_size == 1
    assert br.shares[0].value == "AMD"
    assert br.shares[0].share == Decimal("1.0000")

    notebooks = share_of_shelf_by_brand(
        session, scope=SosScope(country_code="US", product_type="notebook")
    )
    # US eligible notebooks: 10 - 1 desktop = 9
    assert notebooks.universe_size == 9

    asus = share_of_shelf_by_brand(
        session, scope=SosScope(country_code="US", oem="Asus")
    )
    # Intel Asus x3 + AMD Asus desktop x1 = 4
    assert asus.universe_size == 4
    by_brand = {s.value: s for s in asus.shares}
    assert by_brand["Intel"].product_count == 3
    assert by_brand["AMD"].product_count == 1

    newegg = share_of_shelf_by_brand(
        session, scope=SosScope(retailer_code="newegg", country_code="US")
    )
    assert newegg.universe_size == 10


def test_oem_drilldown_percentages(session: Session) -> None:
    _seed_known_universe(session)
    result = share_of_shelf_by_oem(session, scope=SosScope(country_code="US"))
    assert result.universe_size == 10
    by_oem = {s.value: s for s in result.shares}
    # Asus: 3 Intel NB + 1 AMD DT = 4 → 40%
    assert by_oem["Asus"].product_count == 4
    assert by_oem["Asus"].share == Decimal("0.4000")
    # MSI: 2 → 20%
    assert by_oem["MSI"].product_count == 2
    assert by_oem["MSI"].share == Decimal("0.2000")
    # Apple OEM: 2 → 20%
    assert by_oem["Apple"].product_count == 2
    assert by_oem["Apple"].share == Decimal("0.2000")


def test_historical_trends_by_date(session: Session) -> None:
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    obs = ObservationRepository(session)
    run = runs.start(retailer_code="newegg", country_code="US", run_type="discovery")

    t0 = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)

    p_intel = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="TREND-INTEL",
        canonical_url="https://example.test/TREND-INTEL",
        title="ASUS ROG Gaming Intel",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        category_raw="Gaming Laptops",
        collection_run_id=run.id,
    )
    p_amd = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="TREND-AMD",
        canonical_url="https://example.test/TREND-AMD",
        title="MSI Gaming AMD",
        brand="AMD",
        oem="MSI",
        product_type="notebook",
        category_raw="Gaming Laptops",
        collection_run_id=run.id,
    )

    # Day 0: only Intel on shelf
    obs.add_snapshot(
        product_id=p_intel.id,
        collection_run_id=run.id,
        observed_at=t0,
        title="ASUS ROG Gaming Intel",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        category_raw="Gaming Laptops",
    )
    # Day 1: Intel + AMD
    obs.add_snapshot(
        product_id=p_intel.id,
        collection_run_id=run.id,
        observed_at=t1,
        title="ASUS ROG Gaming Intel",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        category_raw="Gaming Laptops",
    )
    obs.add_snapshot(
        product_id=p_amd.id,
        collection_run_id=run.id,
        observed_at=t1,
        title="MSI Gaming AMD",
        brand="AMD",
        oem="MSI",
        product_type="notebook",
        category_raw="Gaming Laptops",
    )
    session.commit()

    trends = share_of_shelf_trends(session, scope=SosScope(country_code="US"))
    assert len(trends) == 2
    day0, day1 = trends[0], trends[1]
    assert day0.universe_size == 1
    assert day0.shares[0].value == "Intel"
    assert day0.shares[0].share == Decimal("1.0000")

    assert day1.universe_size == 2
    by_brand = {s.value: s for s in day1.shares}
    assert by_brand["Intel"].share == Decimal("0.5000")
    assert by_brand["AMD"].share == Decimal("0.5000")


def test_furniture_and_other_type_excluded_from_sos_denominator(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="discovery"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="DESK-OTHER-1",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="other",
        title='Electric RGB Gaming Standing Desk 55"',
        category_raw="workstation",
        run_id=run.id,
    )
    _add_product(
        session,
        sku="DESK-WS-1",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="workstation",
        title="E6G CyberX RGB LED Gaming Standing Desk Black",
        category_raw="workstation",
        run_id=run.id,
    )
    _add_product(
        session,
        sku="NB-UNKNOWN-1",
        brand="UNKNOWN",
        oem="UNKNOWN",
        product_type="notebook",
        title="15 Inch Gaming Notebook 16GB RAM RTX 4060",
        run_id=run.id,
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    assert result.universe_size == 1
    by_brand = {s.value: s for s in result.shares}
    assert by_brand["UNKNOWN"].product_count == 1
    assert result.exclusions.accessory_or_ineligible_type >= 2


def test_other_brand_gaming_tablet_included_in_sos_denominator(session: Session) -> None:
    from collector.classification import OTHER

    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="discovery"
    )
    _mark_stratified(run)
    _add_product(
        session,
        sku="TAB-OTHER-1",
        brand=OTHER,
        oem="UNKNOWN",
        product_type="tablet",
        title='HAOVM 10" Gaming Tablet MediaTek Helio G80',
        category_raw="tablet",
        run_id=run.id,
    )
    session.commit()
    result = share_of_shelf_by_brand(session)
    assert result.universe_size == 1
    assert result.shares[0].value == OTHER
    assert result.shares[0].product_count == 1


def test_same_sku_two_strata_counted_once_in_sos() -> None:
    eligible, exclusions = build_eligible_universe(
        [
            {
                "product_id": 1,
                "retailer_code": "newegg",
                "country_code": "US",
                "retailer_sku": "SHARED-SKU",
                "brand": "AMD",
                "oem": "Asus",
                "product_type": "notebook",
                "title": "ASUS TUF Gaming Laptop AMD Ryzen 7",
                "category_raw": "notebook",
            },
            {
                "product_id": 1,
                "retailer_code": "newegg",
                "country_code": "US",
                "retailer_sku": "SHARED-SKU",
                "brand": "AMD",
                "oem": "Asus",
                "product_type": "desktop",
                "title": "ASUS TUF Gaming Laptop AMD Ryzen 7",
                "category_raw": "desktop",
            },
        ]
    )
    assert len(eligible) == 1
    assert exclusions["duplicate_sku"] == 1
    assert eligible[0].brand == "AMD"


def test_build_universe_rejects_accessories_in_pure_function() -> None:
    cfg = load_sos_universe_config()
    eligible, exclusions = build_eligible_universe(
        [
            {
                "product_id": 1,
                "retailer_code": "newegg",
                "country_code": "US",
                "retailer_sku": "A",
                "brand": "Intel",
                "oem": "Asus",
                "product_type": "notebook",
                "title": "ROG Gaming Laptop",
                "category_raw": "Gaming",
            },
            {
                "product_id": 2,
                "retailer_code": "newegg",
                "country_code": "US",
                "retailer_sku": "B",
                "brand": "Intel",
                "oem": "Asus",
                "product_type": "UNKNOWN",
                "title": "Gaming Mouse",
                "category_raw": "accessories mice",
            },
        ],
        config=cfg,
    )
    assert len(eligible) == 1
    assert exclusions["accessory_or_ineligible_type"] == 1
