"""Hardening tests: evidence status, two-stage ML classification, identity tiers."""

from __future__ import annotations

from collector.audit.checks import evaluate_p2, evaluate_p3
from collector.audit.models import AuditContext, ListingEvidence, ProductEvidence, UNKNOWN
from collector.evidence import (
    BLOCKED,
    COMPLETE,
    PARTIAL,
    REASON_ACCOUNT_VERIFICATION,
    listing_only_evidence,
    product_page_evidence,
)
from collector.normalize import detect_product_type
from collector.retailers.mercadolibre.classification import (
    EXCLUDED,
    VALID,
    classify_mercadolibre_product,
    is_collection_eligible,
)
from collector.retailers.mercadolibre.product_page import build_from_listing
from collector.search.models import STATUS_COMPLETE, STATUS_PARTIAL, STATUS_ZERO


def test_evidence_listing_only_is_partial_with_blocked_pdp() -> None:
    bundle = listing_only_evidence(reason=REASON_ACCOUNT_VERIFICATION)
    assert bundle.overall_status == PARTIAL
    assert bundle.surfaces["pdp"].status == BLOCKED
    assert bundle.surfaces["pdp"].reason == REASON_ACCOUNT_VERIFICATION
    assert bundle.surfaces["listing"].status == COMPLETE
    assert not bundle.pdp_inspected()
    assert bundle.pdp_blocked()


def test_evidence_product_page_complete() -> None:
    bundle = product_page_evidence(specs_available=True)
    assert bundle.overall_status == COMPLETE
    assert bundle.pdp_inspected()


def test_p2_unknown_when_pdp_blocked_evidence() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB1",
        source_url="https://www.mercadolivre.com.br/x/p/MLB1",
        title="Notebook ASUS Vivobook AMD Ryzen 7 8GB RAM 512GB SSD",
        price_text="3.999,90",
        list_price_text=None,
        promo_text=None,
        category_raw="MLB1652",
        detail_page_status="account_verification",
    )
    evidence = product.raw_payload["evidence"]
    assert evidence["surfaces"]["pdp"]["status"] == BLOCKED
    ctx = AuditContext(
        retailer_code="mercadolibre",
        country_code="BR",
        brand="AMD",
        oem="Asus",
        product_type="notebook",
        listing=ListingEvidence(title=product.title, available=True),
        product=ProductEvidence(
            title=product.title,
            badges_inspected=False,
            badge_texts=[],
            page_text=None,
            specs_available=False,
            available=True,
        ),
    )
    assert evaluate_p2(ctx).result == UNKNOWN
    assert evaluate_p3(ctx).result == UNKNOWN


def test_classify_valid_gaming_notebook() -> None:
    result = classify_mercadolibre_product(
        title="Notebook Gamer Lenovo LOQ RTX 3050 Intel Core i5 16GB SSD",
        category_raw="search:notebook gamer",
    )
    assert result.status == VALID
    assert result.product_type == "notebook"
    assert result.gaming is True
    assert is_collection_eligible(result)


def test_classify_valid_notebook_english_type() -> None:
    result = classify_mercadolibre_product(
        title="Notebook ASUS Vivobook 15 AMD Ryzen 7 8 GB RAM 512 GB SSD"
    )
    assert result.status == VALID
    assert result.product_type == "notebook"  # English controlled vocabulary


def test_classify_excludes_tv_powerbank_supplement_bike_phone() -> None:
    cases = [
        "Smart Tv Philips 50 4k",
        "Power Bank Turbo 20000mah",
        "Omega Plus 240 Caps Dark Lab",
        "Bicicleta Spinning Sevenfit com computador",
        "Smartphone Samsung Galaxy A54 128GB",
    ]
    for title in cases:
        result = classify_mercadolibre_product(
            title=title, category_raw="notebook_ofertas"
        )
        assert result.status == EXCLUDED, title
        assert result.product_type == "UNKNOWN", title
        assert not is_collection_eligible(result)


def test_weak_computador_alias_does_not_beat_hard_negative() -> None:
    result = classify_mercadolibre_product(
        title="Bicicleta Spinning EPS-988 Com Volante e Computador"
    )
    assert result.status == EXCLUDED
    assert result.hard_negative is True


def test_discovery_slug_does_not_force_type() -> None:
    assert (
        detect_product_type(title="Cabo HDMI 2m", category_raw="notebook_ofertas")
        == "UNKNOWN"
    )
    result = classify_mercadolibre_product(
        title="Cabo HDMI 2m", discovery_name="notebook_ofertas"
    )
    assert result.status != VALID


def test_raw_portuguese_preserved_english_type() -> None:
    product = build_from_listing(
        retailer_code="mercadolibre",
        country_code="BR",
        currency="BRL",
        sku="MLB9",
        source_url="https://www.mercadolivre.com.br/x/p/MLB9",
        title="Notebook Gamer Acer Nitro com processador Intel e placa de vídeo RTX",
        price_text="5.000,00",
        list_price_text=None,
        promo_text="10% OFF",
        category_raw="MLB1652",
        detail_page_status="account_verification",
    )
    assert "processador" in (product.title or "").lower()
    assert product.product_type == "notebook"
    assert product.raw_payload.get("title_raw_language") == "pt-BR"
    assert product.raw_payload["classification"]["status"] == VALID


def test_limit_semantics_irrelevant_and_duplicates() -> None:
    from collector.base import CollectionOutcome

    titles = [
        ("MLB1", "Smart Tv Philips"),
        ("MLB2", "Notebook Acer Aspire AMD Ryzen 5 8GB 512GB SSD"),
        ("MLB2", "Notebook Acer Aspire AMD Ryzen 5 8GB 512GB SSD"),
        ("MLB3", "Power Bank 20000mah"),
        ("MLB4", "Notebook Lenovo IdeaPad Intel Core i5 16GB SSD"),
    ]
    limit = 2
    outcome = CollectionOutcome()
    seen: set[str] = set()
    for sku, title in titles:
        if len(outcome.success) >= limit:
            break
        if sku in seen:
            outcome.skipped_duplicates.append(sku)
            continue
        seen.add(sku)
        result = classify_mercadolibre_product(title=title)
        if not is_collection_eligible(result):
            outcome.skipped_irrelevant.append({"sku": sku, "status": result.status})
            continue
        product = build_from_listing(
            retailer_code="mercadolibre",
            country_code="BR",
            currency="BRL",
            sku=sku,
            source_url=f"https://www.mercadolivre.com.br/p/{sku}",
            title=title,
            price_text="1.000,00",
            list_price_text=None,
            promo_text=None,
            category_raw="MLB1652",
            detail_page_status="listing_only",
        )
        outcome.success.append(product)
    assert len(outcome.success) == 2
    assert len(outcome.skipped_irrelevant) == 2
    assert outcome.skipped_duplicates == ["MLB2"]


def test_fewer_than_limit_valid_products_ok() -> None:
    stream = [
        ("A", "Smart Tv"),
        ("B", "Notebook Dell Intel Core i7 16GB SSD"),
        ("C", "Power Bank"),
    ]
    success = []
    for sku, title in stream:
        result = classify_mercadolibre_product(title=title)
        if is_collection_eligible(result):
            success.append(sku)
    assert success == ["B"]  # stop naturally below limit=20


def test_search_status_constants() -> None:
    assert STATUS_COMPLETE == "COMPLETE"
    assert STATUS_PARTIAL == "PARTIAL"
    assert STATUS_ZERO == "ZERO_RESULTS"


def test_historical_irrelevant_search_excluded_from_sov_filter() -> None:
    from analytics.share_of_voice.queries import _observation_is_eligible
    from types import SimpleNamespace

    junk = SimpleNamespace(
        retailer_code="mercadolibre",
        title="Smart Tv Philips 50",
        details=None,
    )
    good = SimpleNamespace(
        retailer_code="mercadolibre",
        title="Notebook Gamer ASUS TUF RTX 4060",
        details={"is_eligible": True},
    )
    flagged = SimpleNamespace(
        retailer_code="mercadolibre",
        title="Notebook ASUS",
        details={"is_eligible": False},
    )
    assert _observation_is_eligible(junk) is False
    assert _observation_is_eligible(good) is True
    assert _observation_is_eligible(flagged) is False


def test_exact_gtin_matched_tier1() -> None:
    from analytics.product_identity.matching import (
        MATCHED,
        ProductFingerprint,
        score_pair,
    )

    left = ProductFingerprint(
        product_id=1,
        retailer_code="newegg",
        country_code="US",
        retailer_sku="N1",
        title="ASUS ROG",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        gtin="7891234567890",
    )
    right = ProductFingerprint(
        product_id=2,
        retailer_code="mercadolibre",
        country_code="BR",
        retailer_sku="MLB1",
        title="Notebook ASUS",
        brand="Intel",
        oem="Asus",
        product_type="notebook",
        gtin="7891234567890",
    )
    scored = score_pair(left, right)
    assert scored.status == MATCHED
    assert scored.method == "exact_gtin"


def test_title_only_never_matched() -> None:
    from analytics.product_identity.matching import (
        MATCHED,
        POSSIBLE_MATCH,
        ProductFingerprint,
        score_pair,
    )

    left = ProductFingerprint(
        product_id=1,
        retailer_code="newegg",
        country_code="US",
        retailer_sku="N1",
        title="ASUS ROG Strix G16 Gaming Laptop Intel Core Ultra RTX 4070 16GB 1TB",
        brand="Intel",
        oem=None,
        product_type="notebook",
        normalized_title="asus rog strix g16 gaming laptop intel core ultra rtx 4070 16gb 1tb",
        title_tokens=frozenset({"strix", "g16", "4070"}),
    )
    right = ProductFingerprint(
        product_id=2,
        retailer_code="mercadolibre",
        country_code="BR",
        retailer_sku="MLB1",
        title="ASUS ROG Strix G16 Gaming Laptop Intel Core Ultra RTX 4070 16GB 1TB",
        brand="Intel",
        oem=None,
        product_type="notebook",
        normalized_title="asus rog strix g16 gaming laptop intel core ultra rtx 4070 16gb 1tb",
        title_tokens=frozenset({"strix", "g16", "4070"}),
    )
    scored = score_pair(left, right)
    assert scored.status != MATCHED
    assert scored.status in {POSSIBLE_MATCH, "UNMATCHED"} or scored.method == "title_only"
