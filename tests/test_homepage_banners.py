"""Controlled tests for homepage banner detection and Banner Share.

Does not call live retailer websites.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.banner_share import (
    BannerShareScope,
    banner_share_by_brand,
    banner_share_trends,
)
from collector.banners.detect import (
    AMBIGUOUS,
    TRACKED_BRANDS,
    UNKNOWN,
    detect_brand_from_evidence,
    extract_badge_text,
    extract_discount_text,
    is_excluded_region,
    process_banner_candidates,
)
from collector.banners.persist import persist_banners
from database.models import Base, BannerObservation
from database.repositories import CollectionRunRepository, ObservationRepository


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


def test_intel_amd_qualcomm_apple_banner_detection() -> None:
    cases = [
        ("Shop Intel Core Ultra AI PCs", "Intel"),
        ("AMD Ryzen AI gaming desktops", "AMD"),
        ("Powered by Qualcomm Snapdragon X Elite", "Qualcomm"),
        ("Apple MacBook Pro M3 — shop now", "Apple"),
    ]
    for text, expected in cases:
        brand, method, evidence = detect_brand_from_evidence(text=text)
        assert brand == expected
        assert method in {"text", "aria_label", "alt", "title"}
        assert evidence


def test_banner_text_discount_link_badge_extraction() -> None:
    candidates = [
        {
            "tag": "div",
            "class_name": "hero-banner promo",
            "text": "Intel Core Ultra laptops — Save $200 Limited Time",
            "aria_label": "",
            "alt": "Intel promo",
            "title": "",
            "href": "https://www.newegg.com/intel",
            "selector": "div.hero-banner",
            "ancestor_hints": ["main"],
            "position": 1,
        }
    ]
    banners = process_banner_candidates(
        candidates, source_url="https://www.newegg.com/"
    )
    assert len(banners) == 1
    b = banners[0]
    assert b.brand == "Intel"
    assert "Intel" in (b.banner_text or "")
    assert b.discount_text and "Save" in b.discount_text
    assert b.badge_text and "Limited" in b.badge_text
    assert b.link_present is True
    assert b.link_url == "https://www.newegg.com/intel"
    assert b.is_tracked_brand is True


def test_discount_and_badge_helpers() -> None:
    assert extract_discount_text("Up to 30% off GPUs") is not None
    assert extract_badge_text("Exclusive Special Offer weekend") is not None
    assert extract_discount_text("Just a headline") is None


def test_product_card_and_navigation_exclusion() -> None:
    assert is_excluded_region(
        tag="div",
        class_name="item-cell",
        selector="div.item-cell",
        ancestor_hints=["goods-list"],
    )
    assert is_excluded_region(tag="nav", class_name="menu", role="navigation")
    assert is_excluded_region(tag="footer", class_name="site-footer")

    product_card = {
        "tag": "div",
        "class_name": "item-cell",
        "text": "Intel Core Ultra Gaming Laptop $999",
        "href": "https://www.newegg.com/p/123",
        "selector": "div.item-cell",
        "ancestor_hints": ["goods-list"],
        "alt": "",
        "title": "",
        "aria_label": "",
    }
    nav = {
        "tag": "a",
        "class_name": "menu-item",
        "role": "navigation",
        "text": "Intel Components",
        "selector": "nav > a.menu-item",
        "ancestor_hints": ["nav"],
        "href": "/Intel",
        "alt": "",
        "title": "",
        "aria_label": "",
    }
    hero = {
        "tag": "div",
        "class_name": "hero-banner",
        "text": "AMD Ryzen AI promo — Save $150",
        "selector": "div.hero-banner",
        "ancestor_hints": ["main"],
        "href": "/amd",
        "alt": "AMD Ryzen",
        "title": "",
        "aria_label": "",
    }
    banners = process_banner_candidates([product_card, nav, hero])
    assert len(banners) == 1
    assert banners[0].brand == "AMD"


def test_unknown_and_ambiguous_brand_handling() -> None:
    brand, _, _ = detect_brand_from_evidence(text="Summer savings event — shop PCs")
    assert brand == UNKNOWN

    brand2, _, _ = detect_brand_from_evidence(
        text="Intel and AMD jointly featured gaming night"
    )
    assert brand2 == AMBIGUOUS

    banners = process_banner_candidates(
        [
            {
                "tag": "div",
                "class_name": "promo-banner",
                "text": "Back to school laptop deals",
                "selector": "div.promo-banner",
                "ancestor_hints": ["main"],
                "href": "/deals",
                "alt": "",
                "title": "",
                "aria_label": "",
            }
        ]
    )
    assert banners[0].brand == UNKNOWN
    assert banners[0].is_tracked_brand is False


def test_repeated_observations_are_append_only(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="banner"
    )
    t0 = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    banner = process_banner_candidates(
        [
            {
                "tag": "div",
                "class_name": "hero-banner",
                "text": "Intel AI PC launch",
                "selector": "div.hero-banner",
                "ancestor_hints": ["main"],
                "href": "/intel",
                "alt": "Intel",
                "title": "",
                "aria_label": "",
            }
        ],
        source_url="https://www.newegg.com/",
    )[0]

    rows1 = persist_banners(
        session,
        [banner],
        retailer_code="newegg",
        country_code="US",
        collection_run_id=run.id,
        observed_at=t0,
    )
    rows2 = persist_banners(
        session,
        [banner],
        retailer_code="newegg",
        country_code="US",
        collection_run_id=run.id,
        observed_at=t1,
    )
    session.commit()

    assert rows1[0].id != rows2[0].id
    all_rows = session.scalars(
        select(BannerObservation).order_by(BannerObservation.observed_at.asc())
    ).all()
    assert len(all_rows) == 2
    assert all_rows[0].headline_text == "Intel AI PC launch"
    assert all_rows[0].observed_at < all_rows[1].observed_at
    # First row not overwritten
    stale = session.get(BannerObservation, rows1[0].id)
    assert stale is not None
    assert stale.brand_detected == "Intel"


def test_banner_share_calculation_and_unknown_exclusion(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="banner"
    )
    t0 = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    obs = ObservationRepository(session)

    def add(brand: str, tracked: bool, text: str) -> None:
        obs.add_banner(
            collection_run_id=run.id,
            observed_at=t0,
            retailer_code="newegg",
            country_code="US",
            page_type="homepage",
            page_url="https://www.newegg.com/",
            brand_detected=brand,
            headline_text=text,
            is_tracked_brand=tracked,
            link_present=False,
            detection_method="text",
        )

    # 2 Intel, 1 AMD, 1 UNKNOWN → tracked denom = 3; Intel 66.67%, AMD 33.33%
    add("Intel", True, "Intel banner A")
    add("Intel", True, "Intel banner B")
    add("AMD", True, "AMD banner")
    add("UNKNOWN", False, "Generic promo")
    session.commit()

    snap = banner_share_by_brand(session)
    assert snap.total_observations == 4
    assert snap.total_tracked_banners == 3
    assert snap.unknown_or_ambiguous == 1
    by_brand = {r.brand: r for r in snap.shares}
    assert by_brand["Intel"].banner_count == 2
    assert by_brand["Intel"].banner_share == Decimal("0.6667")
    assert by_brand["AMD"].banner_count == 1
    assert by_brand["AMD"].banner_share == Decimal("0.3333")
    assert by_brand["Qualcomm"].banner_count == 0
    assert by_brand["Apple"].banner_count == 0


def test_retailer_filter_and_historical_trends(session: Session) -> None:
    runs = CollectionRunRepository(session)
    obs = ObservationRepository(session)
    t0 = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)

    run_us = runs.start(retailer_code="newegg", country_code="US", run_type="banner")
    run_br = runs.start(
        retailer_code="mercadolibre", country_code="BR", run_type="banner"
    )

    obs.add_banner(
        collection_run_id=run_us.id,
        observed_at=t0,
        retailer_code="newegg",
        country_code="US",
        page_type="homepage",
        brand_detected="Intel",
        headline_text="Intel day0",
        is_tracked_brand=True,
        link_present=False,
    )
    obs.add_banner(
        collection_run_id=run_us.id,
        observed_at=t1,
        retailer_code="newegg",
        country_code="US",
        page_type="homepage",
        brand_detected="AMD",
        headline_text="AMD day1",
        is_tracked_brand=True,
        link_present=False,
    )
    obs.add_banner(
        collection_run_id=run_us.id,
        observed_at=t1,
        retailer_code="newegg",
        country_code="US",
        page_type="homepage",
        brand_detected="Intel",
        headline_text="Intel day1",
        is_tracked_brand=True,
        link_present=False,
    )
    obs.add_banner(
        collection_run_id=run_br.id,
        observed_at=t1,
        retailer_code="mercadolibre",
        country_code="BR",
        page_type="homepage",
        brand_detected="Apple",
        headline_text="Apple BR",
        is_tracked_brand=True,
        link_present=False,
    )
    session.commit()

    newegg = banner_share_by_brand(
        session, scope=BannerShareScope(retailer_code="newegg")
    )
    assert newegg.total_tracked_banners == 3
    assert {r.brand: r.banner_count for r in newegg.shares if r.banner_count} == {
        "Intel": 2,
        "AMD": 1,
    }

    trends = banner_share_trends(
        session, scope=BannerShareScope(retailer_code="newegg")
    )
    days = sorted({p.period_start.date() for p in trends})
    assert len(days) == 2
    day1_intel = next(
        p
        for p in trends
        if p.period_start.date() == t1.date() and p.brand == "Intel"
    )
    assert day1_intel.banner_count == 1
    assert day1_intel.total_tracked_banners == 2
    assert day1_intel.banner_share == Decimal("0.5000")


def test_alt_title_priority_and_tracked_brands_constant() -> None:
    assert set(TRACKED_BRANDS) == {"Intel", "AMD", "Qualcomm", "Apple"}
    brand, method, _ = detect_brand_from_evidence(
        text="",
        alt="Qualcomm Snapdragon laptop festival",
    )
    assert brand == "Qualcomm"
    assert method == "alt"
