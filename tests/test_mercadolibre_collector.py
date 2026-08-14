"""Mercado Libre adapter unit tests — fixtures only, no live website."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from analytics.share_of_shelf.universe import load_sos_universe_config
from collector.normalize import build_normalized_product, parse_price
from collector.parsers.badges import BadgeEvidence, evaluate_badges
from collector.persist import CollectionPersister
from collector.retailers.mercadolibre.listing import (
    dedupe_candidates,
    extract_mlb_id,
    parse_listing_card,
)
from collector.retailers.mercadolibre.product_page import (
    build_from_listing,
    specs_from_title,
)
from collector.search.collect import (
    build_mercadolibre_fallback_url,
    build_mercadolibre_search_url,
)
from database.models import Base, PriceHistory, Product, ProductSnapshot


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


def test_extract_mlb_catalog_and_item_ids() -> None:
    assert (
        extract_mlb_id(
            "https://www.mercadolivre.com.br/foo/p/MLB49089309?wid=MLB5382062300"
        )
        == "MLB49089309"
    )
    assert (
        extract_mlb_id(
            "https://produto.mercadolivre.com.br/MLB-2794030545-kit-cuecas-_JM"
        )
        == "MLB2794030545"
    )


def test_parse_listing_card_identity_and_country_fields() -> None:
    cand = parse_listing_card(
        title="Notebook ASUS Vivobook 15 AMD Ryzen 7 8 GB RAM 512 GB SSD",
        href="https://www.mercadolivre.com.br/notebook-asus/p/MLB49089309#frag",
        price_text="3.999,90",
        list_price_text="4.499,90",
        promo_text="11% OFF",
        category_raw="notebook_ofertas",
    )
    assert cand is not None
    assert cand.retailer_sku == "MLB49089309"
    assert cand.source_url.endswith("/p/MLB49089309")
    assert "?" not in cand.source_url
    assert cand.price_text == "3.999,90"


def test_dedupe_candidates_by_sku() -> None:
    a = parse_listing_card(
        title="Notebook A",
        href="https://www.mercadolivre.com.br/a/p/MLB111",
        price_text="1.000,00",
    )
    b = parse_listing_card(
        title="Notebook A again",
        href="https://www.mercadolivre.com.br/a/p/MLB111?x=1",
        price_text="1.050,00",
    )
    c = parse_listing_card(
        title="Notebook B",
        href="https://www.mercadolivre.com.br/b/p/MLB222",
        price_text="2.000,00",
    )
    assert a and b and c
    unique = dedupe_candidates([a, b, c])
    assert len(unique) == 2
    assert {u.retailer_sku for u in unique} == {"MLB111", "MLB222"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("R$ 3.999,90", Decimal("3999.90")),
        ("3.999,90", Decimal("3999.90")),
        ("3999,90", Decimal("3999.90")),
        ("$1,234.56", Decimal("1234.56")),
        (None, None),
        ("", None),
    ],
)
def test_parse_price_brl_and_usd(text, expected) -> None:
    assert parse_price(text) == expected


def test_build_from_listing_brand_oem_type_price() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB49089309",
        source_url="https://www.mercadolivre.com.br/x/p/MLB49089309",
        title="Notebook ASUS Vivobook 15 AMD Ryzen 7 8 GB RAM 512 GB SSD",
        price_text="3.999,90",
        list_price_text="4.499,90",
        promo_text="11% OFF",
        category_raw="notebook_ofertas",
        detail_page_status="account_verification",
    )
    assert product.retailer_code == "mercadolibre"
    assert product.country_code == "BR"
    assert product.currency == "BRL"
    assert product.retailer_sku == "MLB49089309"
    assert product.price_amount == Decimal("3999.90")
    assert product.list_price == Decimal("4499.90")
    assert product.discount_pct is not None
    assert product.is_on_promotion is True
    assert product.oem == "Asus"
    assert product.brand in {"AMD", "UNKNOWN"}  # Ryzen → AMD
    assert product.brand == "AMD"
    assert product.product_type == "notebook"
    assert product.processor
    assert "Ryzen" in product.processor
    assert product.raw_payload.get("detail_page_status") == "account_verification"


def test_missing_fields_stay_null() -> None:
    product = build_normalized_product(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        retailer_sku="MLB9",
        source_url="https://www.mercadolivre.com.br/x/p/MLB9",
        title="Cabo USB generico",
        price_text=None,
        list_price_text=None,
        promo_text=None,
    )
    assert product.price_amount is None
    assert product.list_price is None
    assert product.discount_pct is None
    assert product.promo_text is None


def test_duplicate_prevention_upsert(session: Session) -> None:
    persister = CollectionPersister(session)
    run = persister.start_run(
        retailer_code="mercadolibre", country_code="BR", run_type="pricing"
    )
    session.commit()
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB49089309",
        source_url="https://www.mercadolivre.com.br/x/p/MLB49089309",
        title="Notebook ASUS Vivobook 15 AMD Ryzen 7 8GB RAM 512GB SSD",
        price_text="3.999,90",
        list_price_text=None,
        promo_text=None,
        category_raw="notebook_ofertas",
        detail_page_status="listing_only",
    )
    pid1 = persister.save_product(product, collection_run_id=run.id)
    session.commit()
    product.price_amount = Decimal("3899.90")
    pid2 = persister.save_product(product, collection_run_id=run.id)
    session.commit()
    assert pid1 == pid2
    assert session.scalar(select(func.count()).select_from(Product)) == 1
    assert session.scalar(select(func.count()).select_from(ProductSnapshot)) == 2
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 2


def test_newegg_and_ml_same_sku_string_are_distinct(session: Session) -> None:
    persister = CollectionPersister(session)
    run = persister.start_run(
        retailer_code="multi", country_code="XX", run_type="pricing"
    )
    session.commit()
    ne = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="SKU123",
        source_url="https://www.newegg.com/p/SKU123",
        title="Laptop Intel",
        price_text="999.00",
    )
    ml = build_normalized_product(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        retailer_sku="SKU123",
        source_url="https://www.mercadolivre.com.br/p/MLB1",
        title="Notebook Intel",
        price_text="5.000,00",
    )
    # Force same sku string for identity-boundary test
    ml.retailer_sku = "SKU123"
    persister.save_product(ne, collection_run_id=run.id)
    persister.save_product(ml, collection_run_id=run.id)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Product)) == 2


def test_badge_detection_from_title_evidence() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB1",
        source_url="https://www.mercadolivre.com.br/x/p/MLB1",
        title="Notebook Dell Intel Core Ultra 7 16GB RAM",
        price_text="6.000,00",
        list_price_text=None,
        promo_text=None,
        category_raw="notebook",
        detail_page_status="listing_only",
    )
    evidence = BadgeEvidence(
        badge_texts=[product.title or ""],
        page_text=product.title,
        source_url=product.source_url,
    )
    evaluation = evaluate_badges(
        processor=product.processor,
        title=product.title,
        brand=product.brand,
        evidence=evidence,
    )
    assert evaluation.expected or evaluation.detected or evaluation.ambiguous


def test_s1_p5_evidence_unknown_when_sparse() -> None:
    from collector.audit.engine import run_audits
    from collector.audit.models import AuditContext, ListingEvidence, ProductEvidence

    ctx = AuditContext(
        retailer_code="mercadolibre",
        country_code="BR",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        listing=ListingEvidence(available=False),
        product=ProductEvidence(available=False),
    )
    results = run_audits(ctx)
    assert {r.check_code for r in results} == {"S1", "S2", "P1", "P2", "P3", "P4", "P5"}
    assert all(r.result == "UNKNOWN" for r in results)


def test_share_of_shelf_includes_mercadolibre_candidates() -> None:
    from analytics.share_of_shelf.universe import build_eligible_universe

    cfg = load_sos_universe_config()
    rows = [
        {
            "product_id": 1,
            "retailer_code": "mercadolibre",
            "country_code": "BR",
            "retailer_sku": "MLB1",
            "brand": "AMD",
            "oem": "Asus",
            "product_type": "notebook",
            "title": "Notebook Gamer ASUS Ryzen",
            "category_raw": "notebook gamer",
        },
        {
            "product_id": 2,
            "retailer_code": "mercadolibre",
            "country_code": "BR",
            "retailer_sku": "MLB2",
            "brand": "Intel",
            "oem": None,
            "product_type": "UNKNOWN",
            "title": "Cabo HDMI",
            "category_raw": "cabo",
        },
    ]
    universe, _exclusions = build_eligible_universe(rows, config=cfg)
    assert any(p.retailer_sku == "MLB1" for p in universe)
    assert all(p.retailer_sku != "MLB2" for p in universe)


def test_search_url_builders() -> None:
    assert "lista.mercadolivre.com.br" in build_mercadolibre_search_url("notebook gamer")
    assert "ofertas?q=" in build_mercadolibre_fallback_url("notebook gamer")


def test_specs_from_title() -> None:
    specs = specs_from_title(
        "Notebook Acer Aspire AMD Ryzen 5 8GB RAM 512GB SSD RTX 4050"
    )
    assert "Processador" in specs
    assert "Memória RAM" in specs
    assert "Armazenamento" in specs
    assert "Placa de vídeo" in specs


# ---------------------------------------------------------------------------
# Blocker 1 — discovery pollution / valid-product universe
# ---------------------------------------------------------------------------


def test_ml_tv_not_classified_as_notebook() -> None:
    from collector.normalize import UNKNOWN, detect_product_type

    title = "Smart Tv Philips 50 4k 50pug7300 Comando De Voz Bluetooth"
    assert detect_product_type(title=title, category_raw="notebook_ofertas") == UNKNOWN
    assert detect_product_type(title=title, category_raw="notebook_gamer_ofertas") == UNKNOWN


def test_ml_power_bank_not_classified_as_notebook() -> None:
    from collector.normalize import UNKNOWN, detect_product_type

    title = (
        "Carregador Portátil Power Bank Turbo 20000mah 22.5w Universal "
        "Para iPhone Samsung"
    )
    assert detect_product_type(title=title, category_raw="notebook_gamer_ofertas") == UNKNOWN


def test_ml_supplement_not_classified_as_notebook() -> None:
    from collector.normalize import UNKNOWN, detect_product_type

    title = "Omega Plus 240 Caps Dark Lab"
    assert detect_product_type(title=title, category_raw="notebook_gamer_ofertas") == UNKNOWN


def test_ml_exercise_bike_not_classified_via_computador_substring() -> None:
    from collector.normalize import UNKNOWN, detect_product_type

    title = "Bicicleta Spinning Sevenfit EPS-988 Com Volante de 6 kg e Computador"
    assert detect_product_type(title=title, category_raw="ofertas_query") == UNKNOWN


def test_ml_valid_notebook_classified() -> None:
    from collector.normalize import detect_product_type

    title = (
        "Notebook Gamer Lenovo LOQ 15irx9 Intel Core i5-13450HX 16gb 512gb "
        "SSD RTX 3050 Windows 11"
    )
    assert detect_product_type(title=title, category_raw="MLB1652") == "notebook"
    # Discovery slug alone must not be required — title evidence is enough.
    assert detect_product_type(title=title, category_raw="notebook_ofertas") == "notebook"


def test_discovery_slug_alone_does_not_force_notebook() -> None:
    from collector.normalize import UNKNOWN, detect_product_type

    assert (
        detect_product_type(title="Cabo USB-C generico 2m", category_raw="notebook_ofertas")
        == UNKNOWN
    )


def test_irrelevant_listing_still_consumes_observation_slot() -> None:
    from collector.observation import ObservationCounters, observation_bucket
    from collector.normalize import build_normalized_product
    from collector.retailers.mercadolibre.relevance import is_in_collection_scope

    tv = build_normalized_product(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        retailer_sku="MLBTV1",
        source_url="https://www.mercadolivre.com.br/p/MLBTV1",
        title="Smart Tv 43 Aoc Led Roku Full Hd",
        category_raw="notebook_gamer_ofertas",
    )
    nb = build_normalized_product(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        retailer_sku="MLBNB1",
        source_url="https://www.mercadolivre.com.br/p/MLBNB1",
        title="Notebook ASUS Vivobook AMD Ryzen 7 8GB RAM 512GB SSD",
        category_raw="MLB1652",
    )
    assert not is_in_collection_scope(product_type=tv.product_type, title=tv.title)
    assert is_in_collection_scope(product_type=nb.product_type, title=nb.title)

    counters = ObservationCounters(requested=2)
    for product in (tv, nb):
        counters.record(observation_bucket(product), product)
    assert counters.observed == 2
    assert counters.excluded == 1
    assert counters.valid == 1


def test_limit_means_observed_results_not_valid_unique() -> None:
    """limit=N means up to N observed SERP slots; junk still fills a slot."""
    from collector.observation import ObservationCounters, observation_bucket
    from collector.normalize import build_normalized_product

    def _prod(sku: str, title: str):
        return build_normalized_product(
            retailer_code="mercadolibre",
            country_code="BR",
            currency="BRL",
            retailer_sku=sku,
            source_url=f"https://www.mercadolivre.com.br/p/{sku}",
            title=title,
            category_raw="MLB1652",
        )

    stream = [
        _prod("MLB1", "Smart Tv Philips 50 4k"),
        _prod("MLB2", "Notebook Acer Aspire AMD Ryzen 5 8GB 512GB SSD"),
        _prod("MLB2", "Notebook Acer Aspire AMD Ryzen 5 8GB 512GB SSD"),  # dup
        _prod("MLB3", "Power Bank Turbo 20000mah"),
        _prod("MLB4", "Notebook Lenovo IdeaPad Intel Core i5 16GB SSD"),
        _prod("MLB5", "Omega Plus 240 Caps Dark Lab"),
        _prod("MLB6", "Notebook ASUS TUF Gaming AMD Ryzen 7 RTX 4060"),
    ]
    limit = 2
    counters = ObservationCounters(requested=limit)
    seen: set[str] = set()
    kept_skus: list[str] = []
    for product in stream:
        if counters.observed >= limit:
            break
        sku = product.retailer_sku
        duplicate = sku in seen
        seen.add(sku)
        bucket = observation_bucket(product, duplicate=duplicate)
        counters.record(bucket, product)
        kept_skus.append(sku)

    assert counters.observed == 2
    assert kept_skus == ["MLB1", "MLB2"]
    assert counters.excluded == 1
    assert counters.valid == 1
    assert "MLB4" not in kept_skus
    assert "MLB6" not in kept_skus


def test_duplicate_products_do_not_count_twice() -> None:
    from collector.retailers.mercadolibre.listing import dedupe_candidates, parse_listing_card

    cards = []
    for href in (
        "https://www.mercadolivre.com.br/a/p/MLB111",
        "https://www.mercadolivre.com.br/a/p/MLB111?x=1",
        "https://www.mercadolivre.com.br/b/p/MLB222",
    ):
        cards.append(
            parse_listing_card(
                title="Notebook ASUS Ryzen 7",
                href=href,
                price_text="1.000,00",
            )
        )
    unique = dedupe_candidates([c for c in cards if c])
    assert len(unique) == 2


def test_mercadolibre_collector_scope_rejects_irrelevant() -> None:
    from collector.retailers.mercadolibre.collector import MercadoLibreCollector
    from collector.normalize import build_normalized_product

    # Avoid network/config side effects beyond YAML loads.
    collector = object.__new__(MercadoLibreCollector)
    tv = build_normalized_product(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        retailer_sku="X",
        source_url="https://www.mercadolivre.com.br/p/X",
        title="Smart Tv 43 Aoc Led",
    )
    nb = build_normalized_product(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        retailer_sku="Y",
        source_url="https://www.mercadolivre.com.br/p/Y",
        title="Notebook Dell Intel Core Ultra 7 16GB",
    )
    assert collector.is_in_collection_scope(tv) is False
    assert collector.is_in_collection_scope(nb) is True


def test_newegg_scope_unchanged_accepts_all() -> None:
    from collector.retailers.newegg.collector import NeweggCollector
    from collector.normalize import build_normalized_product

    collector = object.__new__(NeweggCollector)
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82E16834204369",
        source_url="https://www.newegg.com/p/N82E16834204369",
        title="ASUS ROG Zephyrus G14 Gaming Laptop AMD Ryzen 9",
        category_raw="gaming_laptops",
    )
    assert collector.is_in_collection_scope(product) is True
    # Newegg type detection still uses category fallback when appropriate.
    from collector.normalize import detect_product_type

    assert (
        detect_product_type(
            title="ASUS ROG Zephyrus G14 Gaming Laptop AMD Ryzen 9",
            category_raw="gaming_laptops",
        )
        == "notebook"
    )


def test_historical_observations_preserved_on_type_correction(session: Session) -> None:
    """Correcting live product_type must not delete prior snapshots/prices."""
    from collector.normalize import UNKNOWN

    persister = CollectionPersister(session)
    run = persister.start_run(
        retailer_code="mercadolibre", country_code="BR", run_type="pricing"
    )
    session.commit()
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB65588451",
        source_url="https://www.mercadolivre.com.br/x/p/MLB65588451",
        title="Carregador Portátil Power Bank Turbo 20000mah",
        price_text="99,90",
        list_price_text=None,
        promo_text=None,
        category_raw="notebook_gamer_ofertas",
        detail_page_status="listing_only",
    )
    # Live persist uses the classifier (power bank → other), not a forced notebook hint.
    product.product_type = "notebook"
    pid = persister.save_product(product, collection_run_id=run.id)
    session.commit()
    assert session.scalar(select(func.count()).select_from(ProductSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 1
    snap = session.scalars(select(ProductSnapshot)).one()
    assert snap.product_type == "other"

    row = session.get(Product, pid)
    assert row is not None
    assert row.product_type == "other"
    row.product_type = UNKNOWN  # later live-row edit must not delete history
    session.commit()

    assert session.scalar(select(func.count()).select_from(ProductSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 1
    snap = session.scalars(select(ProductSnapshot)).one()
    assert snap.product_type == "other"  # historical observation preserved
    assert row.product_type == UNKNOWN
