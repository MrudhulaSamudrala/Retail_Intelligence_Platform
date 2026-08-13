"""Official Mercado Libre API adapter tests — no live HTTP, no Playwright."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from urllib.error import URLError

from collector.evidence import (
    REASON_API_AUTH_FAILED,
    REASON_API_DISABLED,
    REASON_API_ITEM_NOT_FOUND,
    REASON_API_MALFORMED,
    REASON_API_RATE_LIMITED,
    REASON_API_UNAVAILABLE,
)
from collector.persist import CollectionPersister
from collector.retailers.mercadolibre.api.client import MercadoLibreApiClient, STATUS_OK
from collector.retailers.mercadolibre.api.config import MercadoLibreApiConfig, load_api_config
from collector.retailers.mercadolibre.api.enrich import enrich_product
from collector.retailers.mercadolibre.api.merge import merge_stores
from collector.retailers.mercadolibre.api.normalize import apply_api_payload
from collector.retailers.mercadolibre.field_evidence import (
    METHOD_API,
    METHOD_LISTING_CARD,
    SOURCE_API,
    SOURCE_LISTING_CARD,
    ProvenanceStore,
    observation,
)
from collector.retailers.mercadolibre.product_page import build_from_listing
from database.models import Base, PriceHistory, Product, ProductSnapshot


CATALOG_PAYLOAD = {
    "id": "MLB49089309",
    "status": "active",
    "domain_id": "MLB-NOTEBOOKS",
    "permalink": "https://www.mercadolivre.com.br/notebook-asus/p/MLB49089309",
    "name": "Notebook ASUS Vivobook 15 AMD Ryzen 7",
    "attributes": [
        {"id": "BRAND", "name": "Marca", "value_name": "ASUS"},
        {"id": "MODEL", "name": "Modelo", "value_name": "M1502YA"},
        {"id": "GTIN", "name": "Código universal de produto", "value_name": "7891234567890"},
        {
            "id": "PROCESSOR_MODEL",
            "name": "Modelo do processador",
            "value_name": "AMD Ryzen 7 8845HS",
        },
        {"id": "RAM", "name": "Memória RAM", "value_name": "16 GB"},
        {"id": "SSD_CAPACITY", "name": "Capacidade do SSD", "value_name": "512 GB"},
        {"id": "GRAPHICS_PROCESSOR", "name": "Placa de vídeo", "value_name": "AMD Radeon Graphics"},
        {"id": "DISPLAY_SIZE", "name": "Tamanho da tela", "value_name": '15.6"'},
        {"id": "OPERATING_SYSTEM", "name": "Sistema operacional", "value_name": "KeepOS Linux"},
    ],
    "pictures": [{"id": "1"}, {"id": "2"}],
}

ITEM_PAYLOAD = {
    "id": "MLB5382062300",
    "site_id": "MLB",
    "title": "Notebook ASUS Vivobook 15 AMD Ryzen 7 8 GB RAM 512 GB SSD",
    "seller_id": 123,
    "category_id": "MLB1652",
    "price": 2999,
    "original_price": 3999,
    "currency_id": "BRL",
    "available_quantity": 12,
    "catalog_product_id": "MLB49089309",
    "permalink": "https://produto.mercadolivre.com.br/MLB-5382062300",
    "attributes": CATALOG_PAYLOAD["attributes"],
    "pictures": [{"id": "a"}],
    "variations": [],
}


class FakeResponse:
    def __init__(self, status: int, body: dict | str):
        self.status = status
        if isinstance(body, dict):
            self._raw = json.dumps(body).encode("utf-8")
        else:
            self._raw = str(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, routes: dict[str, tuple[int, object]], *, error: Exception | None = None):
        self.routes = routes
        self.error = error
        self.urls: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        if self.error:
            raise self.error
        for key, (status, body) in self.routes.items():
            if key in url:
                return FakeResponse(status, body)
        return FakeResponse(404, {"message": "not_found", "error": "not_found"})


def _cfg(**overrides) -> MercadoLibreApiConfig:
    data = dict(
        enabled=True,
        client_id="id",
        client_secret="secret",
        access_token="token",
        refresh_token="refresh",
        site_id="MLB",
        base_url="https://api.mercadolibre.com",
        timeout_seconds=5.0,
    )
    data.update(overrides)
    return MercadoLibreApiConfig(**data)


def _listing_product():
    return build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB49089309",
        source_url="https://www.mercadolivre.com.br/x/p/MLB49089309",
        title="Notebook ASUS Vivobook 15 AMD Ryzen 7 8 GB RAM 512 GB SSD",
        price_text="2.999,00",
        list_price_text="3.999,00",
        promo_text="25% OFF",
        category_raw="MLB1652",
        detail_page_status="account_verification",
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


def test_api_disabled_without_credentials() -> None:
    cfg = load_api_config(environ={})
    assert cfg.enabled is False
    product = enrich_product(
        _listing_product(), client=MercadoLibreApiClient(cfg, opener=FakeOpener({}))
    )
    assert product.raw_payload["api_status"] == REASON_API_DISABLED
    assert product.processor
    assert product.price_amount == Decimal("2999.00")


def test_successful_catalog_response() -> None:
    opener = FakeOpener({"/products/MLB49089309": (200, CATALOG_PAYLOAD)})
    client = MercadoLibreApiClient(_cfg(), opener=opener)
    product = enrich_product(_listing_product(), client=client)
    assert product.raw_payload["api_status"] == STATUS_OK
    assert product.retailer_sku == "MLB49089309"
    assert product.raw_payload["identity"]["gtin"] == "7891234567890"
    assert product.raw_payload["identity"]["model"] == "M1502YA"
    assert "8845HS" in (product.processor or "")
    assert product.raw_payload["price_source"] == SOURCE_LISTING_CARD
    prov = product.raw_payload["field_provenance"]["fields"]
    assert prov["gtin"]["source"] == SOURCE_API
    assert prov["gtin"]["extraction_method"] == METHOD_API
    assert product.raw_payload["evidence"]["surfaces"]["api"]["status"] == "COMPLETE"
    assert "Marca" in (product.raw_payload.get("specs_raw_labels") or {})


def test_authentication_failure() -> None:
    opener = FakeOpener(
        {
            "/products/": (401, {"message": "invalid access token"}),
            "/items/": (401, {"message": "invalid access token"}),
        }
    )
    client = MercadoLibreApiClient(_cfg(refresh_token=""), opener=opener)
    result = client.lookup("MLB49089309")
    assert result.status == REASON_API_AUTH_FAILED
    product = enrich_product(_listing_product(), client=client)
    assert product.raw_payload["api_status"] == REASON_API_AUTH_FAILED
    assert product.price_amount == Decimal("2999.00")


def test_rate_limiting_short_circuits() -> None:
    opener = FakeOpener({"/products/": (429, {"message": "too many requests"})})
    client = MercadoLibreApiClient(_cfg(), opener=opener)
    result = client.lookup("MLB49089309")
    assert result.status == REASON_API_RATE_LIMITED
    assert all("/items/" not in url for url in opener.urls)


def test_item_not_found() -> None:
    opener = FakeOpener({})
    client = MercadoLibreApiClient(_cfg(), opener=opener)
    result = client.lookup("MLB49089309")
    assert result.status == REASON_API_ITEM_NOT_FOUND


def test_malformed_api_response() -> None:
    opener = FakeOpener({"/products/": (200, "not-json"), "/items/": (200, "not-json")})
    client = MercadoLibreApiClient(_cfg(), opener=opener)
    result = client.lookup("MLB49089309")
    assert result.status == REASON_API_MALFORMED


def test_api_unavailable_network() -> None:
    opener = FakeOpener({}, error=URLError("connection refused"))
    client = MercadoLibreApiClient(_cfg(), opener=opener)
    result = client.lookup("MLB49089309")
    assert result.status == REASON_API_UNAVAILABLE


def test_missing_fields_stay_unknown() -> None:
    thin = {
        "id": "MLB1",
        "name": "Notebook genérico",
        "attributes": [],
        "domain_id": "MLB-NOTEBOOKS",
    }
    opener = FakeOpener({"/products/": (200, thin)})
    product = enrich_product(
        _listing_product(), client=MercadoLibreApiClient(_cfg(), opener=opener)
    )
    unknown = product.raw_payload.get("unknown_fields") or {}
    assert "gtin" in unknown


def test_evidence_merge_api_fills_gaps() -> None:
    base = ProvenanceStore()
    base.fields["title"] = observation(
        "Listing title", source=SOURCE_LISTING_CARD, extraction_method=METHOD_LISTING_CARD
    )
    incoming = ProvenanceStore()
    apply_api_payload(incoming, CATALOG_PAYLOAD, endpoint="/products")
    merged = merge_stores(base, incoming)
    assert merged.get_value("title") == "Listing title"
    assert merged.get_value("gtin") == "7891234567890"
    assert merged.get_value("processor")


def test_source_precedence_keeps_listing_price() -> None:
    base = ProvenanceStore()
    base.fields["price"] = observation(
        "2.999,00",
        source=SOURCE_LISTING_CARD,
        extraction_method=METHOD_LISTING_CARD,
        currency="BRL",
    )
    incoming = ProvenanceStore()
    apply_api_payload(incoming, ITEM_PAYLOAD, endpoint="/items")
    merged = merge_stores(base, incoming)
    assert merged.get_value("price") == "2.999,00"
    assert merged.fields["price"].source == SOURCE_LISTING_CARD
    assert any(c["field"] == "price" for c in merged.conflicts)


def test_conflicting_values_recorded() -> None:
    base = ProvenanceStore()
    base.fields["processor"] = observation(
        "AMD Ryzen 5", source=SOURCE_LISTING_CARD, extraction_method=METHOD_LISTING_CARD
    )
    incoming = ProvenanceStore()
    apply_api_payload(incoming, CATALOG_PAYLOAD, endpoint="/products")
    merged = merge_stores(base, incoming)
    assert "8845HS" in str(merged.get_value("processor"))
    conflict = next(c for c in merged.conflicts if c["field"] == "processor")
    assert conflict["status"] == "CONFLICT"
    assert conflict["selected_source"] == SOURCE_API
    assert conflict["alternate_source"] == SOURCE_LISTING_CARD


def test_duplicate_prevention_same_sku(session: Session) -> None:
    persister = CollectionPersister(session)
    run = persister.start_run(retailer_code="mercadolibre", country_code="BR", run_type="pricing")
    session.commit()
    listing = _listing_product()
    pid1 = persister.save_product(listing, collection_run_id=run.id)
    opener = FakeOpener({"/products/MLB49089309": (200, CATALOG_PAYLOAD)})
    enriched = enrich_product(listing, client=MercadoLibreApiClient(_cfg(), opener=opener))
    pid2 = persister.save_product(enriched, collection_run_id=run.id)
    session.commit()
    assert pid1 == pid2
    assert session.scalar(select(func.count()).select_from(Product)) == 1
    assert session.scalar(select(func.count()).select_from(ProductSnapshot)) == 2


def test_historical_preservation(session: Session) -> None:
    persister = CollectionPersister(session)
    run = persister.start_run(retailer_code="mercadolibre", country_code="BR", run_type="pricing")
    session.commit()
    listing = _listing_product()
    persister.save_product(listing, collection_run_id=run.id)
    session.commit()
    first_price = session.scalars(select(PriceHistory)).one()
    opener = FakeOpener({"/products/MLB49089309": (200, CATALOG_PAYLOAD)})
    enriched = enrich_product(listing, client=MercadoLibreApiClient(_cfg(), opener=opener))
    persister.save_product(enriched, collection_run_id=run.id)
    session.commit()
    assert session.scalar(select(func.count()).select_from(PriceHistory)) == 2
    assert session.get(PriceHistory, first_price.id) is not None
    assert session.get(PriceHistory, first_price.id).price_amount == first_price.price_amount


def test_api_disabled_mode_does_not_call_http() -> None:
    opener = FakeOpener({"/products/": (200, CATALOG_PAYLOAD)})
    client = MercadoLibreApiClient(_cfg(enabled=False, access_token=""), opener=opener)
    product = enrich_product(_listing_product(), client=client)
    assert opener.urls == []
    assert product.raw_payload["api_status"] == REASON_API_DISABLED


def test_newegg_identity_untouched_by_ml_api(session: Session) -> None:
    from collector.normalize import build_normalized_product

    persister = CollectionPersister(session)
    run = persister.start_run(retailer_code="multi", country_code="XX", run_type="pricing")
    session.commit()
    ne = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82E16834204369",
        source_url="https://www.newegg.com/p/N82E16834204369",
        title="ASUS ROG",
        price_text="999.00",
    )
    persister.save_product(ne, collection_run_id=run.id)
    listing = _listing_product()
    persister.save_product(listing, collection_run_id=run.id)
    opener = FakeOpener({"/products/": (200, CATALOG_PAYLOAD)})
    enriched = enrich_product(listing, client=MercadoLibreApiClient(_cfg(), opener=opener))
    persister.save_product(enriched, collection_run_id=run.id)
    session.commit()
    newegg_rows = session.scalars(select(Product).where(Product.retailer_code == "newegg")).all()
    assert len(newegg_rows) == 1
    assert newegg_rows[0].retailer_sku == "N82E16834204369"
