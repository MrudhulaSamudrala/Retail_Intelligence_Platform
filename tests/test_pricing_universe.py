"""Current pricing universe: eligible latest-batch catalog products (no live sites)."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.pricing import (
    PricingScope,
    average_discount,
    average_price_by_brand,
    count_discounted_products,
    list_price_observations,
    price_change_over_time,
)
from analytics.pricing import queries as pricing_queries
from database.models import Base, PriceHistory, Product, SearchObservation
from database.repositories import (
    CollectionRunRepository,
    ObservationRepository,
    ProductRepository,
)


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


def _run(session: Session, *, retailer: str = "newegg", country: str = "US") -> int:
    row = CollectionRunRepository(session).start(
        retailer_code=retailer, country_code=country, run_type="pricing"
    )
    session.flush()
    return int(row.id)


def _add_priced(
    session: Session,
    *,
    run_id: int,
    sku: str,
    title: str,
    brand: str,
    product_type: str,
    price: Decimal,
    list_price: Decimal | None = None,
    discount_pct: Decimal | None = None,
    promo: str | None = None,
    on_promo: bool | None = None,
    currency: str = "USD",
    retailer: str = "newegg",
    country: str = "US",
    observed_at: datetime | None = None,
    oem: str = "Asus",
) -> Product:
    observed = observed_at or datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    products = ProductRepository(session)
    obs = ObservationRepository(session)
    product = products.upsert_identity(
        retailer_code=retailer,
        country_code=country,
        retailer_sku=sku,
        canonical_url=f"https://example.test/{sku}",
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        collection_run_id=run_id,
    )
    list_amt = list_price if list_price is not None else price
    is_promo = bool(promo) if on_promo is None else on_promo
    obs.add_snapshot(
        product_id=product.id,
        collection_run_id=run_id,
        observed_at=observed,
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        price_amount=price,
        list_price=list_amt,
        discount_pct=discount_pct,
        promo_text=promo,
        is_on_promotion=is_promo,
        currency=currency,
        source_url=f"https://example.test/{sku}",
    )
    obs.add_price(
        product_id=product.id,
        collection_run_id=run_id,
        observed_at=observed,
        price_amount=price,
        list_price=list_amt,
        currency=currency,
        discount_pct=discount_pct,
        is_on_promotion=is_promo,
    )
    if promo:
        obs.add_promotion(
            product_id=product.id,
            collection_run_id=run_id,
            observed_at=observed,
            promo_type="deal",
            promo_text=promo,
            raw_text=promo,
        )
    session.flush()
    return product


def _skus(session: Session, **scope_kwargs) -> set[str]:
    rows = list_price_observations(
        session, scope=PricingScope(**scope_kwargs), latest_only=True
    )
    ids = {o.product_id for o in rows}
    if not ids:
        return set()
    products = session.scalars(select(Product).where(Product.id.in_(ids))).all()
    return {p.retailer_sku for p in products}


def test_eligible_notebook_enters_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="NB-1",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1200"),
    )
    session.commit()
    assert "NB-1" in _skus(session, currency="USD")


def test_eligible_desktop_enters_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="DT-1",
        title="MSI Gaming Desktop AMD Ryzen 7",
        brand="AMD",
        product_type="desktop",
        price=Decimal("1500"),
    )
    session.commit()
    assert "DT-1" in _skus(session, currency="USD")


def test_eligible_workstation_enters_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="WS-1",
        title="Dell Precision Gaming Workstation Intel Xeon",
        brand="Intel",
        product_type="workstation",
        price=Decimal("2500"),
    )
    session.commit()
    assert "WS-1" in _skus(session, currency="USD")


def test_eligible_tablet_enters_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="TAB-1",
        title="Lenovo Gaming Tablet AMD Ryzen",
        brand="AMD",
        product_type="tablet",
        price=Decimal("800"),
    )
    session.commit()
    assert "TAB-1" in _skus(session, currency="USD")


def test_eligible_cpu_enters_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="CPU-1",
        title="AMD Ryzen 9 7950X Gaming Processor",
        brand="AMD",
        product_type="cpu",
        price=Decimal("550"),
    )
    session.commit()
    assert "CPU-1" in _skus(session, currency="USD")


def test_eligible_gpu_enters_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="GPU-1",
        title="AMD Radeon RX 7800 XT Gaming Graphics Card",
        brand="AMD",
        product_type="gpu",
        price=Decimal("499"),
    )
    session.commit()
    assert "GPU-1" in _skus(session, currency="USD")


def test_excluded_furniture_with_price_does_not_enter_current_pricing(
    session: Session,
) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="DESK-1",
        title='Electric RGB Gaming Standing Desk 55"',
        brand="UNKNOWN",
        product_type="other",
        price=Decimal("399"),
    )
    _add_priced(
        session,
        run_id=run_id,
        sku="NB-OK",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1200"),
    )
    session.commit()
    assert "DESK-1" not in _skus(session)
    assert "NB-OK" in _skus(session)


def test_product_type_other_does_not_enter_current_pricing(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="CABLE-1",
        title="HDMI accessory cable",
        brand="OTHER",
        product_type="other",
        price=Decimal("20"),
    )
    session.commit()
    assert "CABLE-1" not in _skus(session)


def test_unknown_brand_included_when_type_eligible(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="TAB-U",
        title="Android octa-core gaming tablet",
        brand="UNKNOWN",
        product_type="tablet",
        price=Decimal("400"),
        oem="UNKNOWN",
    )
    session.commit()
    rows = list_price_observations(session, latest_only=True)
    assert any(o.brand == "UNKNOWN" and o.product_type == "tablet" for o in rows)


def test_other_brand_included_when_type_eligible(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="GPU-NV",
        title="NVIDIA GeForce RTX 4070 Gaming Graphics Card",
        brand="OTHER",
        product_type="gpu",
        price=Decimal("600"),
        oem="NVIDIA",
    )
    session.commit()
    rows = list_price_observations(session, latest_only=True)
    assert any(o.brand == "OTHER" and o.product_type == "gpu" for o in rows)


def test_old_laptop_absent_from_latest_collection_not_in_current_pricing(
    session: Session,
) -> None:
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 14, tzinfo=timezone.utc)
    old_run = _run(session)
    _add_priced(
        session,
        run_id=old_run,
        sku="OLD-LAPTOP",
        title="ASUS Gaming Laptop Intel Core i5",
        brand="Intel",
        product_type="notebook",
        price=Decimal("999"),
        observed_at=t0,
    )
    new_run = _run(session)
    _add_priced(
        session,
        run_id=new_run,
        sku="NEW-NB",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1299"),
        observed_at=t1,
    )
    session.commit()
    assert "NEW-NB" in _skus(session)
    assert "OLD-LAPTOP" not in _skus(session)
    series = price_change_over_time(session, scope=PricingScope(currency="USD"))
    days = {p.period_start.date() for p in series}
    assert t0.date() in days


def test_historical_price_history_remains_unchanged(session: Session) -> None:
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    old_run = _run(session)
    product = _add_priced(
        session,
        run_id=old_run,
        sku="KEEP-HIST",
        title="ASUS Gaming Laptop Intel Core i5",
        brand="Intel",
        product_type="notebook",
        price=Decimal("888"),
        observed_at=t0,
    )
    session.commit()
    first = session.scalars(
        select(PriceHistory).where(PriceHistory.product_id == product.id)
    ).one()
    original = (first.id, first.price_amount, first.observed_at)
    new_run = _run(session)
    _add_priced(
        session,
        run_id=new_run,
        sku="NEW-NB",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1299"),
        observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    list_price_observations(session, latest_only=True)
    stale = session.get(PriceHistory, original[0])
    assert stale is not None
    assert stale.price_amount == original[1] == Decimal("888")
    assert stale.observed_at == original[2]
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 2


def test_latest_price_per_product_is_used(session: Session) -> None:
    run_id = _run(session)
    t0 = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=2)
    product = _add_priced(
        session,
        run_id=run_id,
        sku="NB-LATEST",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1000"),
        observed_at=t0,
    )
    ObservationRepository(session).add_price(
        product_id=product.id,
        collection_run_id=run_id,
        observed_at=t1,
        price_amount=Decimal("900"),
        list_price=Decimal("1000"),
        currency="USD",
        discount_pct=Decimal("10"),
        is_on_promotion=True,
    )
    session.commit()
    rows = list_price_observations(session, latest_only=True)
    match = next(o for o in rows if o.product_id == product.id)
    assert match.current_price == Decimal("900")


def test_same_sku_two_strata_remains_one_identity(session: Session) -> None:
    run_id = _run(session)
    t0 = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 8, 14, 11, 0, tzinfo=timezone.utc)
    first = _add_priced(
        session,
        run_id=run_id,
        sku="X-1",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1100"),
        observed_at=t0,
    )
    second = _add_priced(
        session,
        run_id=run_id,
        sku="X-1",
        title="ASUS TUF Gaming Desktop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1050"),
        observed_at=t1,
    )
    session.commit()
    assert first.id == second.id
    products = session.scalars(select(Product).where(Product.retailer_sku == "X-1")).all()
    assert len(products) == 1
    rows = [
        o
        for o in list_price_observations(session, latest_only=True)
        if o.product_id == first.id
    ]
    assert len(rows) == 1
    assert rows[0].current_price == Decimal("1050")
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 2


def test_usd_and_brl_are_never_combined(session: Session) -> None:
    us_run = _run(session, retailer="newegg", country="US")
    br_run = _run(session, retailer="mercadolibre", country="BR")
    _add_priced(
        session,
        run_id=us_run,
        sku="US-1",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1000"),
        currency="USD",
        retailer="newegg",
        country="US",
    )
    _add_priced(
        session,
        run_id=br_run,
        sku="BR-1",
        title="Notebook Gamer Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("5000"),
        currency="BRL",
        retailer="mercadolibre",
        country="BR",
    )
    session.commit()
    summaries = average_price_by_brand(session)
    currencies = {row.currency for row in summaries}
    assert currencies == {"USD", "BRL"}
    by_ccy = {(row.value, row.currency): row.average_price for row in summaries}
    assert by_ccy[("Intel", "USD")] == Decimal("1000.0")
    assert by_ccy[("Intel", "BRL")] == Decimal("5000.0")


def test_promotion_text_cleared_when_latest_observation_off_promo(
    session: Session,
) -> None:
    run_id = _run(session)
    t0 = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=3)
    product = _add_priced(
        session,
        run_id=run_id,
        sku="NB-PROMO",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1000"),
        list_price=Decimal("1200"),
        discount_pct=Decimal("16.6667"),
        promo="Save $200",
        on_promo=True,
        observed_at=t0,
    )
    ObservationRepository(session).add_price(
        product_id=product.id,
        collection_run_id=run_id,
        observed_at=t1,
        price_amount=Decimal("1200"),
        list_price=Decimal("1200"),
        currency="USD",
        discount_pct=None,
        is_on_promotion=False,
    )
    session.commit()
    row = next(
        o
        for o in list_price_observations(session, latest_only=True)
        if o.product_id == product.id
    )
    assert row.is_on_promotion is False
    assert row.promotion_text is None


def test_average_discount_ignores_null_discount_pct(session: Session) -> None:
    run_id = _run(session)
    _add_priced(
        session,
        run_id=run_id,
        sku="NB-D",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("900"),
        list_price=Decimal("1200"),
        discount_pct=Decimal("25"),
        promo="Save",
    )
    _add_priced(
        session,
        run_id=run_id,
        sku="NB-F",
        title="MSI Gaming Laptop AMD Ryzen 7",
        brand="AMD",
        product_type="notebook",
        price=Decimal("2000"),
        discount_pct=None,
    )
    session.commit()
    assert average_discount(session, scope=PricingScope(currency="USD")) == Decimal("25")
    assert count_discounted_products(session, scope=PricingScope(currency="USD")) == 1


def test_historical_daily_series_uses_observations_not_unique_products(
    session: Session,
) -> None:
    run_id = _run(session)
    day = datetime(2026, 8, 14, tzinfo=timezone.utc)
    product = _add_priced(
        session,
        run_id=run_id,
        sku="NB-TS",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1000"),
        observed_at=day.replace(hour=10),
    )
    ObservationRepository(session).add_price(
        product_id=product.id,
        collection_run_id=run_id,
        observed_at=day.replace(hour=18),
        price_amount=Decimal("950"),
        currency="USD",
        is_on_promotion=False,
    )
    session.commit()
    series = price_change_over_time(session, scope=PricingScope(currency="USD"))
    assert len(series) == 1
    assert series[0].observation_count == 2


def test_pricing_does_not_use_fixed_100_denominator(session: Session) -> None:
    run_id = _run(session)
    for index in range(3):
        _add_priced(
            session,
            run_id=run_id,
            sku=f"NB-{index}",
            title=f"ASUS TUF Gaming Laptop Intel Core i7 {index}",
            brand="Intel",
            product_type="notebook",
            price=Decimal("1000") + index,
        )
    session.commit()
    summaries = average_price_by_brand(session, scope=PricingScope(currency="USD"))
    assert summaries[0].product_count == 3
    assert summaries[0].product_count != 100


def test_pricing_does_not_depend_on_search_observations(session: Session) -> None:
    run_id = _run(session)
    product = _add_priced(
        session,
        run_id=run_id,
        sku="NB-SO",
        title="ASUS TUF Gaming Laptop Intel Core i7",
        brand="Intel",
        product_type="notebook",
        price=Decimal("1111"),
    )
    session.add(
        SearchObservation(
            retailer_code="newegg",
            country_code="US",
            keyword="gaming laptop",
            position=1,
            retailer_sku="NB-SO",
            title=product.title,
            product_id=product.id,
            brand="Intel",
            is_sponsored=False,
            collection_status="COMPLETE",
            observed_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
            observation_source="stratified_catalog",
            stratum="notebook",
        )
    )
    session.commit()
    source = inspect.getsource(pricing_queries)
    assert "SearchObservation" not in source
    assert "search_observations" not in source
    rows = list_price_observations(session, latest_only=True)
    assert len(rows) == 1
    assert rows[0].current_price == Decimal("1111")
    assert rows[0].source == "price_history"
