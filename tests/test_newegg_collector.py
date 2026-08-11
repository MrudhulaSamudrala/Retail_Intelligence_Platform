"""Unit tests for Newegg parsing and shared normalization (no live network)."""

from __future__ import annotations

from decimal import Decimal

from collector.normalize import (
    UNKNOWN,
    build_normalized_product,
    detect_brand,
    detect_oem,
    detect_product_type,
    parse_price,
)
from collector.retailers.newegg.listing import (
    dedupe_candidates,
    extract_item_number,
    parse_listing_card_html,
)
from collector.retailers.newegg.product_page import is_bot_challenge, pick_spec
from collector.base import ListingCandidate


def test_extract_item_number_from_url_and_title() -> None:
    url = "https://www.newegg.com/asus-rog/p/N82E16834204369"
    assert extract_item_number(url) == "N82E16834204369"
    assert extract_item_number("https://www.newegg.com/p/3D5-000W-00001") == "3D5-000W-00001"
    assert extract_item_number("https://www.newegg.com/p/pl?d=gaming+laptop") is None


def test_parse_listing_card_and_dedupe() -> None:
    a = parse_listing_card_html(
        title="ASUS ROG Zephyrus G14 Gaming Laptop AMD Ryzen 9",
        href="/asus-rog/p/N82E16834204369",
        price_text="$1,499.99",
        list_price_text="$1,699.99",
        promo_text="Save $200",
        category_raw="gaming_laptops",
    )
    b = parse_listing_card_html(
        title="ASUS ROG Zephyrus G14 Gaming Laptop AMD Ryzen 9 duplicate",
        href="https://www.newegg.com/asus-rog/p/N82E16834204369?Item=N82E16834204369",
        price_text="$1,499.99",
        list_price_text=None,
        promo_text=None,
    )
    assert a is not None and b is not None
    unique = dedupe_candidates([a, b])
    assert len(unique) == 1
    assert unique[0].retailer_sku == "N82E16834204369"
    assert (
        parse_listing_card_html(
            title="Search page",
            href="https://www.newegg.com/p/pl?d=gaming+laptop",
            price_text=None,
            list_price_text=None,
            promo_text=None,
        )
        is None
    )


def test_brand_oem_and_product_type_detection() -> None:
    title = "ASUS ROG Strix G16 Gaming Laptop Intel Core Ultra 9 RTX 4070"
    assert detect_brand(title) == "Intel"
    assert detect_oem(title, product_type="notebook") == "Asus"
    assert detect_product_type(title=title, category_raw="gaming_laptops") == "notebook"

    cpu_title = "AMD Ryzen 7 7800X3D Processor"
    assert detect_brand(cpu_title) == "AMD"
    assert detect_product_type(title=cpu_title, category_raw="CPU") == "cpu"
    assert detect_oem(cpu_title, product_type="cpu") == UNKNOWN

    # "hp" must not match inside "HDMI"
    msi_title = "MSI Stealth 16 AI Intel Core Ultra7 HDMI ports"
    assert detect_oem(msi_title, product_type="notebook") == "MSI"


def test_build_normalized_product_preserves_specs_and_unknowns() -> None:
    product = build_normalized_product(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="N82E16834204369",
        source_url="https://www.newegg.com/p/N82E16834204369",
        title="Random Gaming Device Without Clear Chip Brand",
        price_text="$999.99",
        availability_text="In stock",
        processor=None,
        gpu=None,
    )
    # May still be UNKNOWN if no brand evidence.
    assert product.brand in {"Intel", "AMD", "Qualcomm", "Apple", UNKNOWN}
    assert product.price_amount == Decimal("999.99")
    assert product.currency == "USD"
    assert product.availability == "in_stock"
    assert product.raw_payload["processor"] is None


def test_parse_price_and_bot_challenge_and_specs() -> None:
    assert parse_price("$1,299.99") == Decimal("1299.99")
    assert parse_price(None) is None
    assert is_bot_challenge("Our system have detected unusual traffic from this device")
    assert not is_bot_challenge("ASUS ROG Zephyrus product page")
    specs = {"CPU Type": "AMD Ryzen 9 8945HS", "Memory": "32GB"}
    assert pick_spec(specs, ["cpu", "cpu type", "processor"]) == "AMD Ryzen 9 8945HS"
    assert pick_spec(specs, ["ram", "memory"]) == "32GB"


def test_qualcomm_and_apple_detection() -> None:
    assert detect_brand("Lenovo Yoga Snapdragon X Elite") == "Qualcomm"
    assert detect_oem("Lenovo Yoga Snapdragon X Elite", product_type="notebook") == "Lenovo"
    assert detect_brand("Apple MacBook Pro M3") == "Apple"
    assert detect_oem("Apple MacBook Pro M3", product_type="notebook") == "Apple"
