"""Controlled fixture tests for pricing / promotion analytics.

Uses in-memory SQLite — synthetic mechanism fixtures only, not real retailer data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.pricing import (
    PricingScope,
    average_discount,
    average_price_by_brand,
    compare_by_country,
    compare_by_product_type,
    compare_by_retailer,
    count_discounted_products,
    discount_change_over_time,
    list_price_observations,
    list_snapshot_pricing_rows,
    median_price_by_brand,
    price_change_over_time,
)
from database.models import Base, PriceHistory, ProductSnapshot
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


def _seed_catalog(session: Session) -> dict[str, int]:
    """Insert controlled products + multi-day price/promo/snapshot history."""
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    obs = ObservationRepository(session)

    run = runs.start(retailer_code="newegg", country_code="US", run_type="pricing")
    t0 = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)

    catalog = [
        # sku, brand, type, retailer, country, currency,
        # (day0 price, list, disc%, promo), (day1 price, list, disc%, promo)
        (
            "SKU-INTEL-NB-1",
            "Intel",
            "notebook",
            "newegg",
            "US",
            "USD",
            (Decimal("1000"), Decimal("1200"), Decimal("16.6667"), "Save $200"),
            (Decimal("900"), Decimal("1200"), Decimal("25.0000"), "Save $300"),
        ),
        (
            "SKU-INTEL-NB-2",
            "Intel",
            "notebook",
            "newegg",
            "US",
            "USD",
            (Decimal("2000"), Decimal("2000"), Decimal("0"), None),
            (Decimal("2000"), Decimal("2000"), Decimal("0"), None),
        ),
        (
            "SKU-AMD-DT-1",
            "AMD",
            "desktop",
            "newegg",
            "US",
            "USD",
            (Decimal("1500"), Decimal("1800"), Decimal("16.6667"), "Desktop deal"),
            (Decimal("1400"), Decimal("1800"), Decimal("22.2222"), "Desktop deal"),
        ),
        (
            "SKU-AMD-NB-BR",
            "AMD",
            "notebook",
            "mercadolibre",
            "BR",
            "BRL",
            (Decimal("5000"), Decimal("6000"), Decimal("16.6667"), "Promo BR"),
            (Decimal("4800"), Decimal("6000"), Decimal("20.0000"), "Promo BR"),
        ),
    ]

    ids: dict[str, int] = {}
    for sku, brand, ptype, retailer, country, currency, day0, day1 in catalog:
        product = products.upsert_identity(
            retailer_code=retailer,
            country_code=country,
            retailer_sku=sku,
            canonical_url=f"https://example.test/{sku}",
            brand=brand,
            oem="TestOEM",
            product_type=ptype,
            collection_run_id=run.id,
        )
        ids[sku] = product.id
        for observed, (price, list_price, disc, promo) in ((t0, day0), (t1, day1)):
            on_promo = bool(promo) or (disc is not None and disc > 0)
            obs.add_snapshot(
                product_id=product.id,
                collection_run_id=run.id,
                observed_at=observed,
                title=f"{brand} {ptype} {sku}",
                brand=brand,
                oem="TestOEM",
                product_type=ptype,
                availability="in_stock",
                price_amount=price,
                list_price=list_price,
                discount_pct=disc if disc and disc > 0 else None,
                promo_text=promo,
                is_on_promotion=on_promo,
                currency=currency,
                source_url=f"https://example.test/{sku}",
            )
            obs.add_price(
                product_id=product.id,
                collection_run_id=run.id,
                observed_at=observed,
                price_amount=price,
                list_price=list_price,
                currency=currency,
                discount_amount=(list_price - price) if list_price > price else None,
                discount_pct=disc if disc and disc > 0 else None,
                is_on_promotion=on_promo,
            )
            if promo:
                obs.add_promotion(
                    product_id=product.id,
                    collection_run_id=run.id,
                    observed_at=observed,
                    promo_type="deal",
                    promo_text=promo,
                    discount_value=(list_price - price) if list_price > price else None,
                    discount_unit="amount",
                    raw_text=promo,
                )

    runs.complete(run, items_collected=len(catalog))
    session.commit()
    return ids


def test_snapshot_stores_price_promo_fields_and_is_append_only(session: Session) -> None:
    ids = _seed_catalog(session)
    product_id = ids["SKU-INTEL-NB-1"]
    snaps = (
        session.scalars(
            select(ProductSnapshot)
            .where(ProductSnapshot.product_id == product_id)
            .order_by(ProductSnapshot.observed_at.asc())
        ).all()
    )
    assert len(snaps) == 2
    assert snaps[0].price_amount == Decimal("1000")
    assert snaps[0].list_price == Decimal("1200")
    assert snaps[0].discount_pct == Decimal("16.6667")
    assert snaps[0].promo_text == "Save $200"
    assert snaps[0].is_on_promotion is True
    assert snaps[0].observed_at < snaps[1].observed_at

    # Later insert must not overwrite the earlier snapshot.
    first_id = snaps[0].id
    stale = session.get(ProductSnapshot, first_id)
    assert stale is not None
    assert stale.price_amount == Decimal("1000")
    assert stale.promo_text == "Save $200"

    prices = (
        session.scalars(
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.observed_at.asc())
        ).all()
    )
    assert len(prices) == 2
    assert prices[0].price_amount == Decimal("1000")
    assert prices[1].price_amount == Decimal("900")


def test_average_and_median_price_by_brand_uses_latest(session: Session) -> None:
    _seed_catalog(session)
    # USD scope only so Intel/AMD Newegg rows are comparable
    scope = PricingScope(currency="USD")
    summaries = {row.value: row for row in average_price_by_brand(session, scope=scope)}

    # Latest Intel: 900 and 2000 → avg 1450, median 1450
    assert summaries["Intel"].average_price == Decimal("1450.0")
    assert summaries["Intel"].median_price == Decimal("1450.0")
    assert summaries["Intel"].product_count == 2

    # Latest AMD USD desktop only in this currency filter: 1400
    assert summaries["AMD"].average_price == Decimal("1400.0")
    assert summaries["AMD"].median_price == Decimal("1400.0")

    medians = median_price_by_brand(session, scope=scope)
    assert medians[("Intel", "USD")] == Decimal("1450.0")


def test_average_discount_and_discounted_product_count(session: Session) -> None:
    _seed_catalog(session)
    scope = PricingScope(currency="USD")
    # Latest discounts: Intel 25%, Intel none, AMD 22.2222% → avg of non-null = (25+22.2222)/2
    avg = average_discount(session, scope=scope)
    assert avg == Decimal(str((25.0 + 22.2222) / 2))

    # Discounted latest: Intel-NB-1 and AMD-DT-1 (Intel-NB-2 not discounted)
    assert count_discounted_products(session, scope=scope) == 2


def test_price_and_discount_change_over_time(session: Session) -> None:
    _seed_catalog(session)
    scope = PricingScope(currency="USD", retailer_code="newegg", country_code="US")
    prices = price_change_over_time(session, scope=scope)
    discounts = discount_change_over_time(session, scope=scope)
    assert len(prices) == 2
    assert prices[0].period_start < prices[1].period_start
    # Day0 USD newegg: 1000, 2000, 1500 → avg 1500
    assert prices[0].average_price == Decimal("1500.0")
    # Day1: 900, 2000, 1400 → avg 1433.333...
    assert prices[1].average_price == pytest.approx(Decimal("1433.333333333333333333333333"))
    assert discounts[0].average_discount_pct is not None
    assert discounts[1].average_discount_pct is not None
    assert discounts[1].average_discount_pct > discounts[0].average_discount_pct


def test_retailer_country_product_type_comparisons(session: Session) -> None:
    _seed_catalog(session)

    by_retailer = compare_by_retailer(session, scope=PricingScope(currency="USD"))
    assert {row.value for row in by_retailer} == {"newegg"}
    assert by_retailer[0].product_count == 3

    by_country = {
        (row.value, row.currency): row for row in compare_by_country(session)
    }
    assert ("US", "USD") in by_country
    assert ("BR", "BRL") in by_country
    assert by_country[("US", "USD")].product_count == 3
    assert by_country[("BR", "BRL")].average_price == Decimal("4800.0")

    by_type = {
        row.value: row
        for row in compare_by_product_type(session, scope=PricingScope(currency="USD"))
    }
    assert by_type["notebook"].product_count == 2
    assert by_type["desktop"].product_count == 1
    assert by_type["desktop"].average_price == Decimal("1400.0")


def test_list_observations_include_promo_text_and_snapshot_source(
    session: Session,
) -> None:
    _seed_catalog(session)
    latest = list_price_observations(
        session, scope=PricingScope(currency="USD", brand="Intel"), latest_only=True
    )
    by_sku_price = {o.current_price: o for o in latest}
    assert Decimal("900") in by_sku_price
    assert by_sku_price[Decimal("900")].promotion_text == "Save $300"
    assert by_sku_price[Decimal("900")].original_price == Decimal("1200")

    snaps = list_snapshot_pricing_rows(
        session,
        scope=PricingScope(currency="USD", brand="Intel"),
        latest_only=True,
    )
    assert all(s.source == "product_snapshots" for s in snaps)
    assert any(s.promotion_text == "Save $300" for s in snaps)


def test_historical_price_rows_never_overwritten(session: Session) -> None:
    ids = _seed_catalog(session)
    product_id = ids["SKU-AMD-DT-1"]
    first = session.scalars(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.observed_at.asc())
    ).first()
    assert first is not None
    original_amount = first.price_amount
    original_id = first.id

    # Append another observation; prior row must remain untouched.
    ObservationRepository(session).add_price(
        product_id=product_id,
        observed_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        price_amount=Decimal("1300"),
        list_price=Decimal("1800"),
        currency="USD",
        discount_pct=Decimal("27.7778"),
        is_on_promotion=True,
    )
    session.commit()

    stale = session.get(PriceHistory, original_id)
    assert stale is not None
    assert stale.price_amount == original_amount == Decimal("1500")
