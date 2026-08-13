"""Unit tests for Mercado Libre multi-layer extraction (no live website)."""

from __future__ import annotations

from collector.audit.checks import evaluate_p3
from collector.audit.engine import build_product_evidence_from_normalized
from collector.audit.models import FAIL, UNKNOWN, AuditContext
from collector.evidence import REASON_PDP_BLOCKED, REASON_SPECS_NOT_FOUND
from collector.retailers.mercadolibre.field_evidence import ProvenanceStore
from collector.retailers.mercadolibre.layers import (
    apply_embedded_or_network,
    apply_json_ld,
    apply_listing_card,
    parse_embedded_script_text,
    parse_json_ld_products,
    specs_from_title,
)
from collector.retailers.mercadolibre.product_page import build_from_listing
from collector.retailers.mercadolibre.pt_labels import english_spec_key, normalize_spec_map


def test_portuguese_spec_labels_map_to_english() -> None:
    assert english_spec_key("Processador") == "processor"
    assert english_spec_key("memória ram") == "ram"
    assert english_spec_key("Armazenamento") == "storage"
    assert english_spec_key("Tela") == "display"
    assert english_spec_key("Sistema operacional") == "operating_system"
    assert english_spec_key("Placa de vídeo") == "gpu"
    specs = normalize_spec_map(
        {"Processador": "AMD Ryzen 7", "Memória RAM": "16 GB", "Tela": '15.6"'}
    )
    assert specs["processor"] == "AMD Ryzen 7"
    assert specs["ram"] == "16 GB"
    assert specs["display"] == '15.6"'


def test_raw_portuguese_keys_preserved_on_listing_product() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB1",
        source_url="https://www.mercadolivre.com.br/x/p/MLB1",
        title="Notebook ASUS Vivobook AMD Ryzen 7 16GB RAM 512GB SSD Windows 11 15.6",
        price_text="4.999,00",
        list_price_text="5.499,00",
        promo_text="9% OFF",
        category_raw="MLB1652",
        detail_page_status="account_verification",
        extra_raw={
            "tile_text": "Notebook ASUS",
            "badge_alts": ["AMD Ryzen"],
            "attributes": ["16 GB RAM", "512 GB SSD"],
        },
    )
    assert "processador" in (product.title or "").lower() or product.processor
    raw_specs = product.raw_payload.get("specs_raw_labels") or product.raw_payload.get("specs")
    assert raw_specs
    assert any("Processador" in k or "processador" in k.lower() for k in raw_specs)
    assert product.raw_payload["title_raw_language"] == "pt-BR"
    assert product.raw_payload["specs_normalized"]["processor"]
    assert product.price_amount is not None
    assert product.raw_payload["price_source"] == "listing_card"
    assert product.raw_payload["evidence"]["surfaces"]["pdp"]["reason"] == "PDP_BLOCKED"
    assert product.raw_payload["unknown_fields"]  # gtin etc. remain unexplained, not invented
    assert "gtin" in product.raw_payload["unknown_fields"]


def test_json_ld_layer_extracts_identity_and_price() -> None:
    store = ProvenanceStore()
    apply_json_ld(
        store,
        {
            "@type": "Product",
            "name": "Notebook Dell Intel Core i7",
            "sku": "MLB49089309",
            "gtin13": "7891234567890",
            "mpn": "XPS15-2024",
            "brand": {"@type": "Brand", "name": "Dell"},
            "offers": {"price": "5999.90", "priceCurrency": "BRL", "availability": "InStock"},
            "additionalProperty": [
                {"name": "Processador", "value": "Intel Core i7-13620H"},
                {"name": "Memória RAM", "value": "16 GB"},
            ],
        },
    )
    assert store.get_value("title") == "Notebook Dell Intel Core i7"
    assert store.get_value("gtin") == "7891234567890"
    assert store.get_value("mpn") == "XPS15-2024"
    assert store.get_value("oem_raw") == "Dell"
    assert store.get_value("processor") == "Intel Core i7-13620H"
    assert store.fields["price"].extraction_method == "json_ld"
    assert store.fields["price"].currency == "BRL"


def test_embedded_json_attributes() -> None:
    store = ProvenanceStore()
    apply_embedded_or_network(
        store,
        {
            "item": {
                "id": "MLB2794030545",
                "title": "Notebook Gamer Lenovo LOQ",
                "price": 4500,
                "original_price": 5200,
                "currency_id": "BRL",
                "attributes": [
                    {"id": "PROCESSOR", "name": "Processador", "value_name": "AMD Ryzen 7 8845HS"},
                    {"id": "RAM", "name": "Memória RAM", "value_name": "16 GB"},
                ],
            }
        },
        source="embedded_json",
        method="embedded_state",
        layer_name="embedded_json",
    )
    assert "Ryzen 7 8845HS" in str(store.get_value("processor"))
    assert store.get_value("ram") == "16 GB"
    assert store.fields["processor"].source == "embedded_json"


def test_parse_embedded_assignment() -> None:
    parsed = parse_embedded_script_text(
        'window.__PRELOADED_STATE__ = {"title": "Notebook", "price": 10};'
    )
    assert parsed["title"] == "Notebook"


def test_parse_json_ld_graph() -> None:
    raw = [
        '{"@graph":[{"@type":"Product","name":"Notebook Acer","sku":"MLB1"}]}'
    ]
    assert parse_json_ld_products(raw)["name"] == "Notebook Acer"


def test_listing_card_aria_fallback_and_attributes() -> None:
    store = ProvenanceStore()
    apply_listing_card(
        store,
        {
            "href": "https://www.mercadolivre.com.br/foo/p/MLB111",
            "aria_label": "Notebook ASUS Vivobook AMD Ryzen 7",
            "price_text": "3.999,90",
            "attributes": ["16 GB RAM", "512 GB SSD"],
            "badge_alts": ["Intel Evo"],
        },
    )
    assert "ASUS" in (store.get_value("title") or "")
    assert store.get_value("ram") == "16 GB"
    assert store.get_value("storage")


def test_title_heuristics_include_os_and_display() -> None:
    specs = specs_from_title(
        'Notebook Acer 15.6" AMD Ryzen 5 8GB RAM 512GB SSD Windows 11 RTX 4050'
    )
    assert "Sistema operacional" in specs
    assert "Tela" in specs
    assert specs["Processador"] == "AMD Ryzen 5"
    assert "8GB" not in specs["Processador"]


def test_p3_unknown_pdp_blocked_reason() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB1",
        source_url="https://www.mercadolivre.com.br/x/p/MLB1",
        title="Notebook ASUS AMD Ryzen 7 8GB RAM 512GB SSD",
        price_text="3.999,90",
        list_price_text=None,
        promo_text=None,
        category_raw="MLB1652",
        detail_page_status="account_verification",
    )
    ev = build_product_evidence_from_normalized(product)
    assert ev.specs_available is False
    assert ev.access_reason == REASON_PDP_BLOCKED
    ctx = AuditContext(
        retailer_code="mercadolibre",
        country_code="BR",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        product=ev,
    )
    result = evaluate_p3(ctx)
    assert result.result == UNKNOWN
    assert result.details.get("reason") == REASON_PDP_BLOCKED


def test_p3_fail_when_pdp_specs_inspected_without_brand() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB2",
        source_url="https://www.mercadolivre.com.br/x/p/MLB2",
        title="Notebook Dell Intel Core i7 16GB SSD",
        price_text="5.000,00",
        list_price_text=None,
        promo_text=None,
        category_raw="MLB1652",
        detail_page_status="ok",
    )
    product.brand = "Qualcomm"
    product.raw_payload["source"] = "product_page"
    product.raw_payload["detail_page_status"] = "ok"
    product.raw_payload["pdp_accessible"] = True
    product.raw_payload["specs_table_present"] = True
    product.raw_payload["specs"] = {
        "Processador": "Intel Core i7-13620H",
        "Memória RAM": "16 GB",
    }
    ev = build_product_evidence_from_normalized(product)
    assert ev.specs_available is True
    ctx = AuditContext(
        retailer_code="mercadolibre",
        country_code="BR",
        brand="Qualcomm",
        oem="Dell",
        product_type="notebook",
        product=ev,
    )
    result = evaluate_p3(ctx)
    assert result.result == FAIL


def test_p3_unknown_specs_not_found_after_layers() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB3",
        source_url="https://www.mercadolivre.com.br/x/p/MLB3",
        title="Notebook Dell Intel Core i7 16GB SSD",
        price_text="5.000,00",
        list_price_text=None,
        promo_text=None,
        category_raw="MLB1652",
        detail_page_status="ok",
    )
    product.raw_payload["source"] = "product_page"
    product.raw_payload["detail_page_status"] = "ok"
    product.raw_payload["pdp_accessible"] = True
    product.raw_payload["specs_table_present"] = False
    product.raw_payload["evidence"] = {
        "overall_status": "PARTIAL",
        "surfaces": {
            "pdp": {"status": "COMPLETE", "reason": "ok"},
            "specifications": {"status": "UNKNOWN", "reason": REASON_SPECS_NOT_FOUND},
        },
    }
    ev = build_product_evidence_from_normalized(product)
    assert ev.specs_available is False
    assert ev.access_reason == REASON_SPECS_NOT_FOUND
