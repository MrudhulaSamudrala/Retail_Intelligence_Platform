"""Unit tests for cross-retailer identity + dual visibility analytics."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.product_identity.matching import (
    MATCHED,
    POSSIBLE_MATCH,
    UNMATCHED,
    extract_manufacturer_model,
    rebuild_cross_retailer_identity,
    score_pair,
    build_fingerprint,
)
from analytics.product_identity.queries import (
    retailer_product_counts,
    list_common_products,
    list_retailer_only_products,
)
from analytics.product_visibility.queries import (
    highest_cross_retailer_visibility,
    highest_visibility_by_retailer,
    list_product_visibility,
)
from analytics.product_visibility.models import VisibilityScope
from database.models import (
    Base,
    CanonicalProduct,
    Product,
    ProductCrosswalk,
    SearchObservation,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


def _add_product(
    session: Session,
    *,
    retailer: str,
    country: str,
    sku: str,
    title: str,
    brand: str | None = None,
    oem: str | None = None,
    product_type: str = "notebook",
) -> Product:
    p = Product(
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
    session.add(p)
    session.flush()
    return p


def _add_search(
    session: Session,
    *,
    retailer: str,
    country: str,
    keyword: str,
    position: int,
    sku: str | None,
    title: str,
    product_id: int | None = None,
    observed_at: datetime | None = None,
    observation_source: str | None = "stratified_catalog",
    stratum: str | None = "notebook",
    brand: str = "AMD",
) -> None:
    session.add(
        SearchObservation(
            retailer_code=retailer,
            country_code=country,
            keyword=keyword,
            position=position,
            retailer_sku=sku,
            title=title,
            product_id=product_id,
            brand=brand,
            is_sponsored=False,
            collection_status="COMPLETE",
            observed_at=observed_at or datetime.now(timezone.utc),
            observation_source=observation_source,
            stratum=stratum,
        )
    )


def test_retailer_counts_remain_independent(session: Session) -> None:
    for i in range(20):
        _add_product(
            session,
            retailer="newegg",
            country="US",
            sku=f"N{i}",
            title=f"Newegg Laptop {i} MPN-AAA{i}",
            oem="Asus",
            brand="AMD",
        )
        _add_product(
            session,
            retailer="mercadolibre",
            country="BR",
            sku=f"MLB{i}",
            title=f"Notebook ML {i} MPN-BBB{i}",
            oem="Asus",
            brand="Intel",
        )
    session.commit()
    rebuild_cross_retailer_identity(session)
    session.commit()
    counts = retailer_product_counts(session)
    assert counts.newegg == 20
    assert counts.mercadolibre == 20
    assert counts.total_retailer_records == 40
    # Not collapsed to 20 total
    assert counts.newegg != counts.total_retailer_records


def test_same_model_matched_across_retailers(session: Session) -> None:
    p1 = _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="N123",
        title="ASUS ROG Strix G16 G614JZ-XS96",
        oem="Asus",
        brand="Intel",
    )
    p2 = _add_product(
        session,
        retailer="mercadolibre",
        country="BR",
        sku="MLB123",
        title="Notebook ASUS ROG Strix G16 - G614JZ-XS96",
        oem="Asus",
        brand="Intel",
    )
    session.commit()
    rebuild_cross_retailer_identity(session)
    session.commit()
    links = session.scalars(select(ProductCrosswalk)).all()
    by_pid = {c.product_id: c for c in links}
    assert by_pid[p1.id].match_status == MATCHED
    assert by_pid[p2.id].match_status == MATCHED
    assert by_pid[p1.id].canonical_product_id == by_pid[p2.id].canonical_product_id
    common = list_common_products(session)
    assert len(common) == 1


def test_similar_titles_not_auto_matched(session: Session) -> None:
    _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="N1",
        title="ASUS Gaming Laptop 16 inch RTX 4060",
        oem="Asus",
        brand="AMD",
    )
    _add_product(
        session,
        retailer="mercadolibre",
        country="BR",
        sku="MLB1",
        title="Notebook ASUS Gamer 16 polegadas RTX 4060",
        oem="Asus",
        brand="AMD",
    )
    session.commit()
    rebuild_cross_retailer_identity(session)
    session.commit()
    statuses = {c.match_status for c in session.scalars(select(ProductCrosswalk)).all()}
    assert MATCHED not in statuses


def test_newegg_only_excluded_from_common(session: Session) -> None:
    _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="ONLY1",
        title="Lenovo Legion 5 15IAH7",
        oem="Lenovo",
        brand="Intel",
    )
    _add_product(
        session,
        retailer="mercadolibre",
        country="BR",
        sku="OTHER1",
        title="MSI Katana 15 B13VFK",
        oem="MSI",
        brand="Intel",
    )
    session.commit()
    rebuild_cross_retailer_identity(session)
    session.commit()
    assert list_common_products(session) == []
    only = list_retailer_only_products(session, retailer_code="newegg")
    assert any(r.newegg_product_id is not None for r in only)


def test_possible_match_excluded_from_cross_visibility(session: Session) -> None:
    p1 = _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="N9",
        title="Dell G15 Gaming Ryzen 5 8GB 512GB",
        oem="Dell",
        brand="AMD",
    )
    p2 = _add_product(
        session,
        retailer="mercadolibre",
        country="BR",
        sku="MLB9",
        title="Notebook Dell G15 AMD Ryzen 5 8GB RAM 512GB SSD",
        oem="Dell",
        brand="AMD",
    )
    session.commit()
    # Force POSSIBLE_MATCH row manually
    from database.models import CanonicalProduct

    canon = CanonicalProduct(model_name="Dell G15", oem="Dell")
    session.add(canon)
    session.flush()
    session.add(
        ProductCrosswalk(
            canonical_product_id=canon.id,
            product_id=p1.id,
            match_status=POSSIBLE_MATCH,
            match_method="oem_title",
            match_confidence=Decimal("0.55"),
        )
    )
    session.add(
        ProductCrosswalk(
            canonical_product_id=canon.id,
            product_id=p2.id,
            match_status=POSSIBLE_MATCH,
            match_method="oem_title",
            match_confidence=Decimal("0.55"),
        )
    )
    _add_search(
        session,
        retailer="newegg",
        country="US",
        keyword="gaming laptop",
        position=2,
        sku="N9",
        title=p1.title or "",
        product_id=p1.id,
    )
    _add_search(
        session,
        retailer="mercadolibre",
        country="BR",
        keyword="notebook gamer",
        position=1,
        sku="MLB9",
        title=p2.title or "",
        product_id=p2.id,
    )
    session.commit()
    assert highest_cross_retailer_visibility(session, top_n=5) == []


def test_visibility_ranking_and_cross_combined(session: Session) -> None:
    p_ne_hi = _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="N-HI",
        title="ASUS ROG Strix G16 G614JZ-XS96",
        oem="Asus",
        brand="Intel",
    )
    p_ne_lo = _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="N-LO",
        title="Acer Aspire 5 A515-45-R2A3",
        oem="Acer",
        brand="AMD",
    )
    p_ml_hi = _add_product(
        session,
        retailer="mercadolibre",
        country="BR",
        sku="MLB-HI",
        title="Notebook ASUS ROG Strix G16 - G614JZ-XS96",
        oem="Asus",
        brand="Intel",
    )
    session.commit()
    rebuild_cross_retailer_identity(session)
    session.commit()

    # Same observed_at = one search batch (latest-batch dedupe keeps only max ts).
    batch_ne = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    batch_ml = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    for pos in (1, 2, 4, 8):
        _add_search(
            session,
            retailer="newegg",
            country="US",
            keyword="gaming laptop",
            position=pos,
            sku="N-HI",
            title=p_ne_hi.title or "",
            product_id=p_ne_hi.id,
            observed_at=batch_ne,
        )
    _add_search(
        session,
        retailer="newegg",
        country="US",
        keyword="gaming laptop",
        position=15,
        sku="N-LO",
        title=p_ne_lo.title or "",
        product_id=p_ne_lo.id,
        observed_at=batch_ne,
    )
    for pos in (1, 3, 7):
        _add_search(
            session,
            retailer="mercadolibre",
            country="BR",
            keyword="notebook gamer",
            position=pos,
            sku="MLB-HI",
            title=p_ml_hi.title or "",
            product_id=p_ml_hi.id,
            observed_at=batch_ml,
        )
    session.commit()

    top_ne = highest_visibility_by_retailer(session, "newegg", top_n=1)
    assert top_ne and top_ne[0].product_id == p_ne_hi.id
    assert top_ne[0].appearances == 4
    assert top_ne[0].top10_appearances == 4

    top_ml = highest_visibility_by_retailer(session, "mercadolibre", top_n=1)
    assert top_ml and top_ml[0].product_id == p_ml_hi.id

    cross = highest_cross_retailer_visibility(session, top_n=1)
    assert cross
    assert cross[0].combined_appearances == 7
    assert cross[0].newegg_product_id == p_ne_hi.id
    assert cross[0].mercadolibre_product_id == p_ml_hi.id
    assert cross[0].match_status == MATCHED


def test_historical_search_batches_preserved(session: Session) -> None:
    p = _add_product(
        session,
        retailer="newegg",
        country="US",
        sku="N-HIST",
        title="MSI Katana 15 B13VFK",
        oem="MSI",
        brand="Intel",
    )
    t1 = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
    _add_search(
        session,
        retailer="newegg",
        country="US",
        keyword="gaming laptop",
        position=5,
        sku="N-HIST",
        title=p.title or "",
        product_id=p.id,
        observed_at=t1,
    )
    _add_search(
        session,
        retailer="newegg",
        country="US",
        keyword="gaming laptop",
        position=3,
        sku="N-HIST",
        title=p.title or "",
        product_id=p.id,
        observed_at=t2,
    )
    session.commit()
    total = session.scalar(select(func.count()).select_from(SearchObservation))
    assert total == 2
    # Latest-batch scoring uses day2 only → 1 appearance
    rows = list_product_visibility(
        session, VisibilityScope(retailer_code="newegg", top_n=5)
    )
    assert rows and rows[0].appearances == 1
    assert rows[0].average_rank == Decimal("3.00")


def test_extract_manufacturer_model() -> None:
    assert extract_manufacturer_model(
        "Notebook ASUS Vivobook 15 - M1502YA-NJ611"
    ) == "M1502YA-NJ611"


def test_score_pair_title_only_not_matched() -> None:
    left = build_fingerprint(
        Product(
            id=1,
            retailer_code="newegg",
            country_code="US",
            retailer_sku="A",
            canonical_url="u",
            title="ASUS Gaming Laptop 16 RTX 4060",
            oem="Asus",
            brand="AMD",
            product_type="notebook",
        )
    )
    right = build_fingerprint(
        Product(
            id=2,
            retailer_code="mercadolibre",
            country_code="BR",
            retailer_sku="B",
            canonical_url="u",
            title="Notebook ASUS Gamer 16 RTX 4060",
            oem="Asus",
            brand="AMD",
            product_type="notebook",
        )
    )
    # bypass ORM identity: fingerprints already built with ids
    scored = score_pair(left, right)
    assert scored.status != MATCHED
