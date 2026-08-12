"""Tests for append-only historical persistence and product identity.

Uses an isolated in-memory SQLite database so tests do not require PostgreSQL
and never touch production. Fixture rows are synthetic mechanism checks only —
not real retailer data and not production seed data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, PriceHistory, Product
from database.repositories import (
    CollectionRunRepository,
    ObservationRepository,
    ProductRepository,
)


def _as_utc_naive(value: datetime) -> datetime:
    """Normalize datetimes for SQLite round-trips that drop tzinfo."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # SQLite foreign keys are off by default.
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


def test_required_tables_exist(session: Session) -> None:
    expected = {
        "products",
        "product_snapshots",
        "price_history",
        "promotions",
        "retailer_audits",
        "badges",
        "banner_observations",
        "search_observations",
        "collection_runs",
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_product_stable_retailer_identity_and_separate_brand_oem(session: Session) -> None:
    products = ProductRepository(session)
    run = CollectionRunRepository(session).start(
        retailer_code="newegg",
        country_code="US",
        run_type="pricing",
    )

    first = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="N82E168342043XX",
        canonical_url="https://www.newegg.com/example/p/N82E168342043XX",
        title="ASUS ROG Zephyrus G14 AMD Ryzen",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        collection_run_id=run.id,
    )
    session.commit()

    second = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="N82E168342043XX",
        canonical_url="https://www.newegg.com/example/p/N82E168342043XX",
        title="ASUS ROG Zephyrus G14 AMD Ryzen 9",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        collection_run_id=run.id,
    )
    session.commit()

    assert first.id == second.id
    assert second.brand == "AMD"
    assert second.oem == "Asus"
    assert second.retailer_code == "newegg"
    assert second.country_code == "US"
    assert second.product_type == "notebook"
    assert second.title.endswith("Ryzen 9")

    rows = session.scalars(select(Product)).all()
    assert len(rows) == 1


def test_price_history_is_append_only(session: Session) -> None:
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    observations = ObservationRepository(session)

    run1 = runs.start(retailer_code="newegg", country_code="US", run_type="pricing")
    product = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="TEST-SKU-PRICE-1",
        canonical_url="https://www.newegg.com/p/TEST-SKU-PRICE-1",
        brand="Intel",
        oem="Dell",
        product_type="notebook",
        collection_run_id=run1.id,
    )

    t0 = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=8)

    p1 = observations.add_price(
        product_id=product.id,
        collection_run_id=run1.id,
        observed_at=t0,
        price_amount=Decimal("1299.9900"),
        list_price=Decimal("1499.9900"),
        currency="USD",
        is_on_promotion=True,
    )
    runs.complete(run1, items_collected=1)
    session.commit()

    run2 = runs.start(retailer_code="newegg", country_code="US", run_type="pricing")
    p2 = observations.add_price(
        product_id=product.id,
        collection_run_id=run2.id,
        observed_at=t1,
        price_amount=Decimal("1249.9900"),
        list_price=Decimal("1499.9900"),
        currency="USD",
        is_on_promotion=True,
    )
    runs.complete(run2, items_collected=1)
    session.commit()

    history = observations.list_prices(product.id)
    assert len(history) == 2
    assert history[0].id == p1.id
    assert history[1].id == p2.id
    assert history[0].price_amount == Decimal("1299.9900")
    assert history[1].price_amount == Decimal("1249.9900")
    assert history[0].observed_at < history[1].observed_at

    # Prior row must remain unchanged after a later insert.
    stale = session.get(PriceHistory, p1.id)
    assert stale is not None
    assert stale.price_amount == Decimal("1299.9900")
    assert _as_utc_naive(stale.observed_at) == _as_utc_naive(t0)


def test_snapshots_and_related_observations_preserve_timestamps(session: Session) -> None:
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    observations = ObservationRepository(session)

    run = runs.start(retailer_code="newegg", country_code="US", run_type="combined")
    product = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="TEST-SKU-OBS-1",
        canonical_url="https://www.newegg.com/p/TEST-SKU-OBS-1",
        brand="Qualcomm",
        oem="Lenovo",
        product_type="notebook",
        collection_run_id=run.id,
    )

    observed = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

    snap = observations.add_snapshot(
        product_id=product.id,
        collection_run_id=run.id,
        observed_at=observed,
        title="Lenovo Yoga Snapdragon X Elite",
        brand="Qualcomm",
        oem="Lenovo",
        product_type="notebook",
        availability="in_stock",
        price_amount=Decimal("1099.0000"),
        list_price=Decimal("1199.0000"),
        discount_pct=Decimal("8.3403"),
        promo_text="Save $100",
        is_on_promotion=True,
        currency="USD",
        source_url="https://www.newegg.com/p/TEST-SKU-OBS-1",
    )
    promo = observations.add_promotion(
        product_id=product.id,
        collection_run_id=run.id,
        observed_at=observed,
        promo_type="instant_rebate",
        promo_text="Save $100",
        discount_value=Decimal("100.0000"),
        discount_unit="amount",
    )
    audit = observations.add_audit(
        product_id=product.id,
        collection_run_id=run.id,
        observed_at=observed,
        retailer_code="newegg",
        country_code="US",
        brand="Qualcomm",
        product_type="notebook",
        check_code="P1",
        result="PASS",
        evidence_text="Title includes Snapdragon X Elite",
    )
    badge = observations.add_badge(
        product_id=product.id,
        collection_run_id=run.id,
        observed_at=observed,
        badge_code="best_seller",
        badge_text="Best Seller",
        is_relevant=True,
    )
    banner = observations.add_banner(
        collection_run_id=run.id,
        observed_at=observed,
        retailer_code="newegg",
        country_code="US",
        page_type="homepage",
        banner_position=1,
        brand_detected="AMD",
        oem_detected=None,
        headline_text="AMD Ryzen AI PCs",
        discount_text="Save $100",
        badge_text="Limited Time",
        link_present=True,
        destination_url="https://www.newegg.com/amd",
        is_tracked_brand=True,
        evidence_text="AMD Ryzen AI PCs",
        selector="div.hero-banner",
        detection_method="text",
    )
    search = observations.add_search(
        collection_run_id=run.id,
        product_id=product.id,
        observed_at=observed,
        retailer_code="newegg",
        country_code="US",
        keyword="gaming laptop",
        position=3,
        page_number=1,
        retailer_sku="TEST-SKU-OBS-1",
        title="Lenovo Yoga Snapdragon X Elite",
        brand="Qualcomm",
        oem="Lenovo",
        is_sponsored=False,
    )
    runs.complete(run, items_collected=1)
    session.commit()

    assert _as_utc_naive(snap.observed_at) == _as_utc_naive(observed)
    assert snap.list_price == Decimal("1199.0000")
    assert snap.discount_pct == Decimal("8.3403")
    assert snap.promo_text == "Save $100"
    assert snap.is_on_promotion is True
    assert _as_utc_naive(promo.observed_at) == _as_utc_naive(observed)
    assert _as_utc_naive(audit.observed_at) == _as_utc_naive(observed)
    assert _as_utc_naive(badge.observed_at) == _as_utc_naive(observed)
    assert _as_utc_naive(banner.observed_at) == _as_utc_naive(observed)
    assert _as_utc_naive(search.observed_at) == _as_utc_naive(observed)

    assert len(observations.list_snapshots(product.id)) == 1
    assert len(observations.list_promotions(product.id)) == 1
    assert len(observations.list_audits(product_id=product.id, check_code="P1")) == 1
    assert len(observations.list_badges(product.id)) == 1
    assert len(observations.list_banners("newegg", "US")) == 1
    assert len(observations.list_searches("newegg", "US", "gaming laptop")) == 1


def test_second_snapshot_does_not_overwrite_first(session: Session) -> None:
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    observations = ObservationRepository(session)

    run = runs.start(retailer_code="mercadolibre", country_code="BR", run_type="pricing")
    product = products.upsert_identity(
        retailer_code="mercadolibre",
        country_code="BR",
        retailer_sku="MLB-TEST-001",
        canonical_url="https://www.mercadolivre.com.br/p/MLB-TEST-001",
        brand="Apple",
        oem="Apple",
        product_type="notebook",
        collection_run_id=run.id,
    )

    t0 = datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=8)

    observations.add_snapshot(
        product_id=product.id,
        collection_run_id=run.id,
        observed_at=t0,
        title="MacBook Air M3",
        brand="Apple",
        oem="Apple",
        product_type="notebook",
        availability="in_stock",
        price_amount=Decimal("7999.0000"),
        currency="BRL",
    )
    observations.add_snapshot(
        product_id=product.id,
        collection_run_id=run.id,
        observed_at=t1,
        title="MacBook Air M3 16GB",
        brand="Apple",
        oem="Apple",
        product_type="notebook",
        availability="out_of_stock",
        price_amount=Decimal("7599.0000"),
        currency="BRL",
    )
    session.commit()

    history = observations.list_snapshots(product.id)
    assert len(history) == 2
    assert history[0].price_amount == Decimal("7999.0000")
    assert history[0].availability == "in_stock"
    assert history[1].price_amount == Decimal("7599.0000")
    assert history[1].availability == "out_of_stock"
    assert history[0].observed_at < history[1].observed_at


def test_null_oem_allowed_for_component(session: Session) -> None:
    products = ProductRepository(session)
    product = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="TEST-CPU-001",
        canonical_url="https://www.newegg.com/p/TEST-CPU-001",
        title="AMD Ryzen 7 7800X3D",
        brand="AMD",
        oem=None,
        product_type="cpu",
    )
    session.commit()

    loaded = products.get_by_retailer_sku("newegg", "US", "TEST-CPU-001")
    assert loaded is not None
    assert loaded.brand == "AMD"
    assert loaded.oem is None
    assert loaded.product_type == "cpu"
