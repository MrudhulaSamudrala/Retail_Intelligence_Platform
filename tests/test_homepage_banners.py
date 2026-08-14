"""Controlled tests for homepage banner detection and Banner Share.

Does not call live retailer websites.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

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


def test_banner_step_persists_fields_and_collection_run_id(session: Session) -> None:
    from collector.banners.collect import HomepageInspectionResult
    from collector.orchestration.steps import run_banners_step

    intel = process_banner_candidates(
        [
            {
                "tag": "div",
                "class_name": "hero-banner",
                "text": "Intel Core Ultra — Save $200 Limited Time",
                "href": "https://www.newegg.com/intel",
                "selector": "div.hero-banner",
                "ancestor_hints": ["main"],
                "alt": "Intel",
                "title": "",
                "aria_label": "",
                "position": 1,
            }
        ],
        source_url="https://www.newegg.com/",
    )[0]
    apple = process_banner_candidates(
        [
            {
                "tag": "div",
                "class_name": "hero-banner",
                "text": "Apple MacBook Pro M3 — oferta",
                "href": "https://www.mercadolivre.com.br/apple",
                "selector": "div.hero-banner",
                "ancestor_hints": ["main"],
                "alt": "Apple",
                "title": "",
                "aria_label": "",
                "position": 1,
            }
        ],
        source_url="https://www.mercadolivre.com.br/",
    )[0]
    inspections = [
        HomepageInspectionResult(
            retailer_code="newegg",
            country_code="US",
            homepage_url="https://www.newegg.com/",
            inspected=True,
            banners=[intel],
        ),
        HomepageInspectionResult(
            retailer_code="mercadolibre",
            country_code="BR",
            homepage_url="https://www.mercadolivre.com.br/",
            inspected=True,
            banners=[apple],
        ),
    ]

    parent = CollectionRunRepository(session).start(
        retailer_code="multi",
        country_code="XX",
        run_type="production",
        run_metadata={"source": "test_banner_step"},
    )
    session.commit()
    parent_run_id = parent.id

    async def _run():
        with patch(
            "collector.banners.collect.collect_homepage_banners",
            new=AsyncMock(return_value=inspections),
        ):
            return await run_banners_step(session, parent_run_id=parent_run_id)

    result = asyncio.run(_run())
    assert result.status == "SUCCESS"
    assert result.records_processed == 2
    rows = session.scalars(select(BannerObservation).order_by(BannerObservation.id)).all()
    assert len(rows) == 2
    by_retailer = {r.retailer_code: r for r in rows}
    us = by_retailer["newegg"]
    br = by_retailer["mercadolibre"]
    assert us.collection_run_id == parent_run_id
    assert br.collection_run_id == parent_run_id
    assert us.collection_run_id == br.collection_run_id
    assert us.brand_detected == "Intel"
    assert us.link_present is True
    assert us.destination_url == "https://www.newegg.com/intel"
    assert us.discount_text and "Save" in us.discount_text
    assert us.badge_text and "Limited" in us.badge_text
    assert us.country_code == "US"
    assert br.brand_detected == "Apple"
    assert br.country_code == "BR"
    first_ids = {r.id for r in rows}

    extra = process_banner_candidates(
        [
            {
                "tag": "div",
                "class_name": "hero-banner",
                "text": "AMD Ryzen AI promo",
                "href": "https://www.newegg.com/amd",
                "selector": "div.hero-banner",
                "ancestor_hints": ["main"],
                "alt": "AMD",
                "title": "",
                "aria_label": "",
            }
        ],
        source_url="https://www.newegg.com/",
    )[0]
    persist_banners(
        session,
        [extra],
        retailer_code="newegg",
        country_code="US",
        collection_run_id=us.collection_run_id,
    )
    session.commit()
    later = session.scalars(select(BannerObservation)).all()
    assert len(later) == 3
    assert first_ids.issubset({r.id for r in later})
    still = session.get(BannerObservation, us.id)
    assert still is not None
    assert still.brand_detected == "Intel"
    assert still.headline_text == us.headline_text


def test_dashboard_does_not_treat_missing_banners_as_zero_share() -> None:
    from dashboard.views.banners import banner_tracking_available, tracked_brand_banner_shares
    from analytics.banner_share.models import BannerShareRow
    from decimal import Decimal

    assert banner_tracking_available(0) is False
    assert banner_tracking_available(4) is True
    unknown_only = [
        BannerShareRow(
            brand="UNKNOWN",
            banner_count=18,
            total_tracked_banners=0,
            banner_share=Decimal("0"),
        )
    ]
    assert tracked_brand_banner_shares(unknown_only) == []
    zeros = [
        BannerShareRow(brand=b, banner_count=0, total_tracked_banners=0, banner_share=Decimal("0"))
        for b in ("Intel", "AMD", "Qualcomm", "Apple")
    ]
    assert tracked_brand_banner_shares(zeros) == []
    real = [
        BannerShareRow(brand="Intel", banner_count=2, total_tracked_banners=2, banner_share=Decimal("1"))
    ]
    assert [s.brand for s in tracked_brand_banner_shares(real)] == ["Intel"]


def test_unknown_banners_do_not_create_intel_amd_percentages(session: Session) -> None:
    run = CollectionRunRepository(session).start(
        retailer_code="newegg", country_code="US", run_type="banner"
    )
    t0 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    obs = ObservationRepository(session)
    for i in range(18):
        obs.add_banner(
            collection_run_id=run.id,
            observed_at=t0,
            retailer_code="newegg",
            country_code="US",
            page_type="homepage",
            page_url="https://www.newegg.com/",
            brand_detected="UNKNOWN",
            headline_text=f"Generic promo {i}",
            is_tracked_brand=False,
            link_present=i < 12,
            detection_method="text",
        )
    session.commit()
    snap = banner_share_by_brand(session)
    assert snap.total_observations == 18
    assert snap.total_tracked_banners == 0
    assert snap.unknown_or_ambiguous == 18
    tracked = {r.brand: r for r in snap.shares if r.brand in TRACKED_BRANDS}
    assert tracked == {}
    from dashboard.views.banners import tracked_brand_banner_shares

    assert tracked_brand_banner_shares(snap.shares) == []


def test_banner_kpi_counts_and_unknown_ambiguous_from_observations() -> None:
    from types import SimpleNamespace

    from dashboard.views.banners import banner_kpi_counts, unknown_ambiguous_count

    rows = [
        SimpleNamespace(
            brand_detected="AMD",
            link_present=True,
            discount_text="Save $150",
            badge_text=None,
        ),
        SimpleNamespace(
            brand_detected="Intel",
            link_present=True,
            discount_text=None,
            badge_text=None,
        ),
        SimpleNamespace(
            brand_detected="UNKNOWN",
            link_present=True,
            discount_text="60% off",
            badge_text=None,
        ),
        SimpleNamespace(
            brand_detected="AMBIGUOUS",
            link_present=False,
            discount_text=None,
            badge_text=None,
        ),
        SimpleNamespace(
            brand_detected=None,
            link_present=False,
            discount_text=None,
            badge_text=None,
        ),
    ]
    counts = banner_kpi_counts(rows)
    assert counts.total == 5
    assert counts.linked == 3
    assert counts.discounted == 2
    assert counts.badged == 0
    assert counts.unknown_or_ambiguous == 3
    assert unknown_ambiguous_count(rows) == 3


def test_banner_retailer_filter_uses_scoped_observation_rows() -> None:
    from types import SimpleNamespace

    from dashboard.views.banners import banner_kpi_counts

    newegg = [
        SimpleNamespace(
            brand_detected="AMD",
            retailer_code="newegg",
            link_present=True,
            discount_text=None,
            badge_text=None,
        ),
        SimpleNamespace(
            brand_detected="UNKNOWN",
            retailer_code="newegg",
            link_present=True,
            discount_text=None,
            badge_text=None,
        ),
    ]
    ml = [
        SimpleNamespace(
            brand_detected="UNKNOWN",
            retailer_code="mercadolibre",
            link_present=True,
            discount_text="60% off",
            badge_text=None,
        )
        for _ in range(3)
    ]
    all_counts = banner_kpi_counts(newegg + ml)
    assert all_counts.total == 5
    assert all_counts.unknown_or_ambiguous == 4
    newegg_counts = banner_kpi_counts(newegg)
    assert newegg_counts.total == 2
    assert newegg_counts.unknown_or_ambiguous == 1
    ml_counts = banner_kpi_counts(ml)
    assert ml_counts.total == 3
    assert ml_counts.discounted == 3
    assert ml_counts.unknown_or_ambiguous == 3


def test_highest_tracked_brand_insight_is_dynamic() -> None:
    from analytics.banner_share.models import BannerShareRow
    from dashboard.views.banners import (
        NO_TRACKED_BRAND_INSIGHT,
        highest_tracked_brand_insight,
        tracked_brand_banner_shares,
    )

    empty = tracked_brand_banner_shares([])
    assert highest_tracked_brand_insight(empty) == NO_TRACKED_BRAND_INSIGHT
    assert (
        highest_tracked_brand_insight(empty, retailer_code="mercadolibre")
        == NO_TRACKED_BRAND_INSIGHT
    )

    amd_lead = [
        BannerShareRow(
            brand="AMD",
            banner_count=4,
            total_tracked_banners=6,
            banner_share=Decimal("0.6667"),
        ),
        BannerShareRow(
            brand="Intel",
            banner_count=2,
            total_tracked_banners=6,
            banner_share=Decimal("0.3333"),
        ),
    ]
    assert highest_tracked_brand_insight(amd_lead) == (
        "AMD has the highest observed tracked-brand homepage presence."
    )
    assert highest_tracked_brand_insight(amd_lead, retailer_code="newegg") == (
        "AMD has the highest observed tracked-brand homepage presence on Newegg."
    )

    intel_lead = [
        BannerShareRow(
            brand="Intel",
            banner_count=3,
            total_tracked_banners=3,
            banner_share=Decimal("1"),
        )
    ]
    assert "Intel" in highest_tracked_brand_insight(intel_lead)
    assert "AMD" not in highest_tracked_brand_insight(intel_lead)


def test_no_tracked_brand_evidence_empty_state_copy() -> None:
    from analytics.banner_share.models import BannerShareRow
    from dashboard.views.banners import (
        NO_TRACKED_BRAND_EVIDENCE,
        tracked_brand_banner_shares,
    )

    unknown_only = [
        BannerShareRow(
            brand="UNKNOWN",
            banner_count=35,
            total_tracked_banners=0,
            banner_share=Decimal("0"),
        )
    ]
    zeros = [
        BannerShareRow(
            brand=b, banner_count=0, total_tracked_banners=0, banner_share=Decimal("0")
        )
        for b in ("Intel", "AMD", "Qualcomm", "Apple")
    ]
    assert tracked_brand_banner_shares(unknown_only) == []
    assert tracked_brand_banner_shares(zeros) == []
    assert NO_TRACKED_BRAND_EVIDENCE == "No tracked-brand banner evidence observed."


def test_banner_observations_expander_records_keep_ambiguous() -> None:
    from types import SimpleNamespace

    from dashboard.views.banners import (
        OBSERVATIONS_EXPANDER_LABEL,
        banner_observation_records,
    )

    assert OBSERVATIONS_EXPANDER_LABEL == "View banner observations"
    rows = [
        SimpleNamespace(
            brand_detected="AMBIGUOUS",
            retailer_code="newegg",
            headline_text="AMD Ryzen and Intel Core combo",
            discount_text=None,
            link_present=True,
            badge_text=None,
        ),
        SimpleNamespace(
            brand_detected="UNKNOWN",
            retailer_code="mercadolibre",
            headline_text="Moda no precinho",
            discount_text="60% off",
            link_present=True,
            badge_text=None,
        ),
    ]
    records = banner_observation_records(rows)
    assert [r["Brand"] for r in records] == ["UNKNOWN", "AMBIGUOUS"]
    assert records[1]["Brand"] == "AMBIGUOUS"
    assert records[1]["Retailer"] == "Newegg"
    assert records[0]["Retailer"] == "Mercado Libre"
    assert records[0]["Discount"] == "Yes"
    assert records[0]["Badge"] == "No"


def test_explicit_brand_phrases_and_oem_non_matches() -> None:
    cases = [
        ("AMD Ryzen gaming laptops", "AMD"),
        ("Intel Core Ultra laptops", "Intel"),
        ("Core Ultra laptops", "Intel"),
        ("Intel Evo certified notebooks", "Intel"),
        ("Intel vPro business PCs", "Intel"),
        ("Snapdragon powered", "Qualcomm"),
        ("Apple M4", "Apple"),
        ("Apple Silicon MacBook", "Apple"),
        ("Radeon graphics promo", "AMD"),
        ("Gaming laptop deals", UNKNOWN),
        ("ASUS Gaming Laptop", UNKNOWN),
        ("MSI Gaming Laptop", UNKNOWN),
        ("Lenovo ThinkPad deals", UNKNOWN),
        ("Dell XPS laptops", UNKNOWN),
        ("HP Pavilion sale", UNKNOWN),
        ("Acer Nitro gaming", UNKNOWN),
    ]
    for text, expected in cases:
        brand, _, _ = detect_brand_from_evidence(text=text)
        assert brand == expected, f"{text!r} -> {brand}, expected {expected}"


def test_run15_newegg_hero_brand_comes_from_href_not_headline() -> None:
    """Run 15 stored 'Performance Built for the Win' with AMD in destination/image path."""
    href = (
        "https://www.newegg.com/AMD-Laptops-and-Gaming-Laptops/EventSaleStore/ID-1230"
        "?cm_sp=Homepage-Top2021-_-top-_-Brand+Promo%2fAMD%2fNB%2fY-_-"
        "%2f%2fpromotions.newegg.com%2famd%2f26-0429%2f1150x320.jpg&icid=800714"
    )
    brand, method, evidence = detect_brand_from_evidence(
        text="Performance Built for the Win",
        alt="Performance Built for the Win",
        href=href,
    )
    assert brand == "AMD"
    assert method == "href"
    assert evidence

    banners = process_banner_candidates(
        [
            {
                "tag": "div",
                "class_name": "swiper-slide",
                "text": "Performance Built for the Win",
                "alt": "Performance Built for the Win",
                "title": "",
                "aria_label": "",
                "href": href,
                "image_url": "https://promotions.newegg.com/amd/26-0429/1150x320.jpg",
                "selector": "div.swiper-slide",
                "ancestor_hints": ["hero-banner"],
                "position": 1,
            }
        ]
    )
    assert len(banners) == 1
    assert banners[0].brand == "AMD"
    assert banners[0].is_tracked_brand is True
    assert banners[0].detection_method in {"href", "image_url"}


def test_run15_generic_newegg_and_mercadolibre_banners_stay_unknown() -> None:
    generic = [
        {
            "text": "Take Class Anywhere",
            "href": (
                "https://www.newegg.com/Take-Class-Anywhere/EventSaleStore/ID-1132"
                "?cm_sp=Homepage-Top2021-_-top-_-nepro%2f26-0731"
            ),
            "alt": "Take Class Anywhere",
        },
        {
            "text": "Newegg Student Store",
            "href": "https://promotions.newegg.com/nepro/24-0162/LP/index.html",
            "alt": "Newegg Student Store",
        },
        {
            "text": "Back-to-School PC Builder 10% Flash Sale",
            "href": "https://www.newegg.com/tools/custom-pc-builder/pl/ID-343",
            "alt": "Back-to-School PC Builder 10% Flash Sale",
        },
        {
            "text": "K-beauty e J-beauty. até 60% off. Consulte termos e condições.",
            "href": "https://click1.mercadolivre.com.br/display/clicks/MLB/count?a=abc",
            "alt": "K-beauty e J-beauty. até 60% off. Consulte termos e condições.",
        },
        {
            "text": "Usamos cookies para melhorar sua experiência no Mercado Livre.",
            "href": "https://www.mercadolivre.com.br/privacidade#tech-and-cookies",
            "alt": "",
        },
    ]
    for raw in generic:
        brand, _, _ = detect_brand_from_evidence(
            text=raw["text"], alt=raw["alt"], href=raw["href"]
        )
        assert brand == UNKNOWN, raw["text"]


def test_image_url_filename_is_banner_evidence() -> None:
    brand, method, _ = detect_brand_from_evidence(
        text="Performance Built for the Win",
        image_url="https://promotions.newegg.com/amd/26-0429/1150x320.jpg",
    )
    assert brand == "AMD"
    assert method == "image_url"


def test_href_oem_or_generic_path_is_not_a_tracked_brand() -> None:
    brand, _, _ = detect_brand_from_evidence(
        text="ASUS Gaming Laptop",
        href="https://www.newegg.com/ASUS-Gaming-Laptops/EventSaleStore/ID-1",
    )
    assert brand == UNKNOWN
    brand2, _, _ = detect_brand_from_evidence(
        text="MSI Gaming Laptop",
        href="https://www.newegg.com/MSI-Gaming-Laptop/EventSaleStore/ID-2",
    )
    assert brand2 == UNKNOWN


def test_short_m_series_tokens_are_not_read_from_tracking_urls() -> None:
    brand, _, _ = detect_brand_from_evidence(
        text="Back-to-School sale",
        href="https://www.newegg.com/deals?m1=tracker&m2=slot",
    )
    assert brand == UNKNOWN
    brand2, method, _ = detect_brand_from_evidence(text="Apple M4")
    assert brand2 == "Apple"
    assert method == "text"
