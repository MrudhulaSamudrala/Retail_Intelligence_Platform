"""Dashboard unit tests — no fake KPI values; uses SQLite fixtures + pure helpers.

Live PostgreSQL connectivity is probed optionally when DATABASE_URL / POSTGRES_*
are configured; otherwise connection probe is skipped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from dashboard.config import alert_thresholds, load_dashboard_config
from dashboard.filters import (
    DashboardFilters,
    clear_filters,
    default_filters,
    previous_period,
    to_pricing_scope,
    to_sos_scope,
    to_sov_scope,
)
from dashboard.queries.collection import count_tracked_products, load_collection_status
from dashboard.services import (
    _count_price_changes,
    build_alerts,
    metric_average_price,
    metric_share_of_shelf,
    metric_tracked_products,
)
from dashboard.utils.format import fmt_change, fmt_pct
from dashboard.utils.semantics import DataState, MetricValue
from database.models import Base, CollectionRunStep, PriceHistory
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


def _seed(session: Session) -> None:
    runs = CollectionRunRepository(session)
    products = ProductRepository(session)
    run = runs.start(retailer_code="newegg", country_code="US", run_type="combined")
    run.status = "partial"
    run.completed_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    session.flush()
    session.add(
        CollectionRunStep(
            collection_run_id=run.id,
            component="mercadolibre",
            status="PARTIAL",
            error_message="PDP account verification",
            records_processed=2,
        )
    )
    session.flush()

    t0 = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    p1 = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="SKU-1",
        canonical_url="https://example.com/1",
        title="Intel Notebook",
        brand="Intel",
        oem="ASUS",
        product_type="notebook",
        collection_run_id=run.id,
    )
    p2 = products.upsert_identity(
        retailer_code="newegg",
        country_code="US",
        retailer_sku="SKU-2",
        canonical_url="https://example.com/2",
        title="AMD Desktop",
        brand="AMD",
        oem="MSI",
        product_type="desktop",
        collection_run_id=run.id,
    )
    session.add_all(
        [
            PriceHistory(
                product_id=p1.id,
                collection_run_id=run.id,
                observed_at=t0,
                price_amount=Decimal("1000"),
                list_price=Decimal("1200"),
                currency="USD",
                discount_pct=Decimal("16.67"),
                is_on_promotion=True,
            ),
            PriceHistory(
                product_id=p1.id,
                collection_run_id=run.id,
                observed_at=t1,
                price_amount=Decimal("900"),
                list_price=Decimal("1200"),
                currency="USD",
                discount_pct=Decimal("25"),
                is_on_promotion=True,
            ),
            PriceHistory(
                product_id=p2.id,
                collection_run_id=run.id,
                observed_at=t1,
                price_amount=Decimal("1500"),
                currency="USD",
                is_on_promotion=False,
            ),
        ]
    )
    session.commit()


def test_dashboard_config_loads():
    cfg = load_dashboard_config()
    assert "dashboard" in cfg
    assert "alerts" in cfg
    thr = alert_thresholds()
    assert "compliance_score_below" in thr


def test_filter_propagation_scopes():
    filters = DashboardFilters(
        retailer_code="newegg",
        country_code="US",
        product_type="notebook",
        brand="Intel",
        oem="ASUS",
        date_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    pricing = to_pricing_scope(filters)
    assert pricing.retailer_code == "newegg"
    assert pricing.brand == "Intel"
    sos = to_sos_scope(filters)
    assert sos.oem == "ASUS"
    sov = to_sov_scope(filters)
    assert sov.country_code == "US"
    cleared = clear_filters()
    assert cleared.retailer_code is None
    prev = previous_period(filters)
    assert prev is not None
    assert prev.date_to == filters.date_from


def test_metric_semantics_no_data_vs_zero():
    empty = MetricValue.from_number(None)
    assert empty.state == DataState.NO_DATA
    assert "No data" in empty.display
    zero = MetricValue.from_number(0, display="0%")
    assert zero.state == DataState.ZERO
    partial = MetricValue.partial("PARTIAL", detail="incomplete")
    assert partial.state == DataState.PARTIAL
    unknown = MetricValue.unknown("PDP blocked")
    assert unknown.state == DataState.UNKNOWN
    blocked = MetricValue.blocked("account verification")
    assert blocked.state == DataState.BLOCKED


def test_fmt_change_insufficient():
    delta, label = fmt_change(None, 1)
    assert delta is None
    assert label == "Insufficient data"
    assert fmt_pct(None) == "No data"
    assert fmt_pct(0) == "0.0%"


def test_tracked_products_and_price_change(session: Session):
    _seed(session)
    filters = DashboardFilters(retailer_code="newegg")
    assert count_tracked_products(session, retailer_code="newegg") == 2
    m = metric_tracked_products(session, filters)
    assert m.value == 2
    assert m.state == DataState.OK
    changed = _count_price_changes(session, filters)
    assert changed == 1  # SKU-1 changed 1000 -> 900; SKU-2 only one obs


def test_average_price_from_db(session: Session):
    _seed(session)
    filters = DashboardFilters(retailer_code="newegg")
    m = metric_average_price(session, filters)
    assert m.state == DataState.OK
    assert m.value is not None
    # latest: 900 and 1500 => avg 1200
    assert abs(float(m.value) - 1200.0) < 0.01


def test_collection_status_partial(session: Session):
    _seed(session)
    snap = load_collection_status(session)
    assert snap.is_partial is True
    assert snap.freshness_label == "PARTIAL DATA"
    assert any(c.status == "PARTIAL" for c in snap.components)
    alerts = build_alerts(
        session,
        DashboardFilters(),
        collection=snap,
        compliance_score=None,
        sos_snap=None,
        sov_snap=None,
    )
    assert any(a.title == "PARTIAL DATA" for a in alerts)
    assert any("mercadolibre" in a.title.lower() for a in alerts)


def test_share_of_shelf_no_fake_when_empty(session: Session):
    # Empty DB — SoS must be NO DATA, not fabricated %
    filters = DashboardFilters()
    m, snap = metric_share_of_shelf(session, filters)
    assert snap.universe_size == 0
    assert m.state == DataState.NO_DATA
    assert m.value is None


def test_retailer_country_brand_oem_product_type_filters(session: Session):
    _seed(session)
    f = DashboardFilters(brand="Intel", product_type="notebook", oem="ASUS", country_code="US")
    m = metric_tracked_products(session, f)
    assert m.value == 1
    f2 = DashboardFilters(brand="Qualcomm")
    m2 = metric_tracked_products(session, f2)
    assert m2.value == 0
    assert m2.state == DataState.ZERO


def test_default_filters_have_date_range():
    f = default_filters()
    assert f.date_from is not None and f.date_to is not None


def test_db_connection_probe_optional():
    from dashboard.db import check_connection

    ok, msg = check_connection()
    assert isinstance(ok, bool)
    assert isinstance(msg, str)
    # If Postgres is unavailable in CI, ok may be False — that is acceptable.
