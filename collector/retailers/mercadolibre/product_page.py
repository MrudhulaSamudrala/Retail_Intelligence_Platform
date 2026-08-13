"""Mercado Libre product-page / listing enrichment parsing."""

from __future__ import annotations

from typing import Any, Optional

from collector.evidence import (
    REASON_OK,
    REASON_SPECS_AVAILABLE,
    REASON_SPECS_NOT_FOUND,
    listing_only_evidence,
    map_block_reason,
    product_page_evidence,
)
from collector.normalize import build_normalized_product
from collector.retailers.mercadolibre.classification import classify_mercadolibre_product
from collector.retailers.mercadolibre.field_evidence import (
    METHOD_EMBEDDED_STATE,
    METHOD_NETWORK_JSON,
    SOURCE_EMBEDDED_JSON,
    SOURCE_NETWORK,
    ProvenanceStore,
)
from collector.retailers.mercadolibre.layers import (
    DOM_EXTRACT_JS,
    EMBEDDED_EXTRACT_JS,
    apply_dom_payload,
    apply_embedded_or_network,
    apply_json_ld,
    apply_listing_card,
    apply_title_heuristics,
    collect_badge_signals,
    parse_embedded_script_text,
    parse_json_ld_products,
    pick_spec,
    specs_from_title,
)
from collector.retailers.mercadolibre.listing import extract_mlb_id
from collector.retailers.mercadolibre.pt_labels import normalize_spec_map
from collector.retailers.mercadolibre.selectors import (
    ACCOUNT_VERIFICATION_MARKERS,
    BOT_CHALLENGE_MARKERS,
    PRODUCT_PRICE_SELECTORS,
    PRODUCT_TITLE_SELECTORS,
    SPEC_ROW_SELECTORS,
)

# Re-exported for existing tests / audit runners.
SPEC_ALIASES = {
    "processor": [
        "processador",
        "processor",
        "cpu",
        "chip",
        "processador (cpu)",
    ],
    "gpu": [
        "placa de vídeo",
        "placa de video",
        "gpu",
        "gráficos",
        "graficos",
        "graphics",
    ],
    "ram": [
        "memória ram",
        "memoria ram",
        "ram",
        "memória",
        "memoria",
    ],
    "storage": [
        "armazenamento",
        "ssd",
        "hd",
        "disco rígido",
        "disco rigido",
        "storage",
    ],
    "display": [
        "tela",
        "display",
        "tamanho da tela",
        "resolução da tela",
        "resolucao da tela",
    ],
    "operating_system": [
        "sistema operacional",
        "sistema operativo",
        "so",
        "os",
        "operating system",
    ],
}

__all__ = [
    "SPEC_ALIASES",
    "build_from_listing",
    "first_text",
    "is_account_verification",
    "is_bot_challenge",
    "parse_product_page",
    "pick_spec",
    "specs_from_title",
]


def is_account_verification(html_or_text: str, url: str = "") -> bool:
    blob = f"{html_or_text or ''} {url or ''}".lower()
    return any(m in blob for m in ACCOUNT_VERIFICATION_MARKERS)


def is_bot_challenge(html_or_text: str) -> bool:
    lowered = (html_or_text or "").lower()
    return any(m in lowered for m in BOT_CHALLENGE_MARKERS)


async def first_text(locator_factory, selectors: list[str]) -> Optional[str]:
    for selector in selectors:
        loc = locator_factory(selector)
        try:
            if await loc.count() == 0:
                continue
            text = await loc.first.inner_text(timeout=2000)
            if text and text.strip():
                return text.strip()
        except Exception:  # noqa: BLE001
            continue
    return None


def _required_fields() -> list[str]:
    return [
        "title",
        "retailer_sku",
        "price",
        "processor",
        "ram",
        "storage",
        "gpu",
        "display",
        "operating_system",
        "gtin",
        "mpn",
        "oem_raw",
    ]


def _build_product(
    *,
    retailer_code: str,
    country_code: str,
    currency: str,
    source_url: str,
    category_raw: Optional[str],
    store: ProvenanceStore,
    fallback_sku: str,
    fallback_title: Optional[str],
    extra_raw: Optional[dict[str, Any]],
    detail_page_status: str,
    evidence_bundle,
    classified,
    badge_signals: Optional[dict[str, list[str]]] = None,
    specs_inspected: bool = False,
    pdp_accessible: bool = False,
    listing_audit: Optional[dict[str, Any]] = None,
) -> Any:
    title = store.get_value("title") or fallback_title
    sku = store.get_value("retailer_sku") or fallback_sku
    if source_url:
        sku = extract_mlb_id(source_url, title or "") or sku
    specs_raw = store.get_value("specs_raw") or {}
    if not isinstance(specs_raw, dict):
        specs_raw = {}
    specs_normalized = normalize_spec_map(specs_raw)
    processor = store.get_value("processor") or pick_spec(specs_raw, SPEC_ALIASES["processor"])
    gpu = store.get_value("gpu") or pick_spec(specs_raw, SPEC_ALIASES["gpu"])
    ram = store.get_value("ram") or store.get_value("memory") or pick_spec(
        specs_raw, SPEC_ALIASES["ram"]
    )
    storage = store.get_value("storage") or pick_spec(specs_raw, SPEC_ALIASES["storage"])
    display = store.get_value("display")
    operating_system = store.get_value("operating_system")
    if display:
        specs_raw.setdefault("Tela", display)
        specs_normalized.setdefault("display", display)
    if operating_system:
        specs_raw.setdefault("Sistema operacional", operating_system)
        specs_normalized.setdefault("operating_system", operating_system)

    discovery_name = None
    if isinstance(extra_raw, dict):
        discovery_name = extra_raw.get("discovery_name")
    if classified is None:
        classified = classify_mercadolibre_product(
            title=title,
            category_raw=category_raw,
            specs=specs_raw,
            discovery_name=str(discovery_name) if discovery_name else None,
        )

    unknown = store.unknown_fields(_required_fields())
    completeness = "COMPLETE"
    if unknown:
        completeness = "PARTIAL"
    if not pdp_accessible:
        completeness = "PARTIAL"

    price_obs = store.fields.get("price")
    list_obs = store.fields.get("list_price")
    raw = {
        "detail_page_status": detail_page_status,
        "source": "product_page" if pdp_accessible else "listing_card",
        "evidence": evidence_bundle.to_dict(),
        "classification": classified.to_dict(),
        "title_raw_language": "pt-BR",
        "specs_table_present": bool(specs_raw) and specs_inspected,
        "specs_normalized": specs_normalized,
        "specs_raw_labels": specs_raw,
        "field_provenance": store.to_dict(),
        "evidence_completeness": completeness,
        "unknown_fields": unknown,
        "identity": {
            "gtin": store.get_value("gtin"),
            "mpn": store.get_value("mpn"),
            "oem_raw": store.get_value("oem_raw"),
            "model": store.get_value("model"),
        },
        "gaming_relevance": "gaming" if classified.gaming else "non_gaming",
        "platform_brand": None,
        "badge_signals": badge_signals or {},
        "listing_audit": listing_audit or {},
        "pdp_accessible": pdp_accessible,
        "price_source": price_obs.source if price_obs else None,
        "price_extraction_method": price_obs.extraction_method if price_obs else None,
        "list_price_source": list_obs.source if list_obs else None,
        **(extra_raw or {}),
    }
    product = build_normalized_product(
        retailer_code=retailer_code,
        country_code=country_code,
        currency=currency,
        retailer_sku=sku,
        source_url=source_url,
        title=title,
        category_raw=category_raw,
        price_text=store.get_value("price"),
        list_price_text=store.get_value("list_price"),
        availability_text=store.get_value("availability"),
        promo_text=store.get_value("promo_text"),
        processor=processor,
        gpu=gpu,
        ram=ram,
        storage=storage,
        specs=specs_raw,
        manufacturer=store.get_value("oem_raw"),
        raw_payload=raw,
    )
    product.product_type = classified.product_type
    product.raw_payload["classification"] = classified.to_dict()
    product.raw_payload["evidence"] = evidence_bundle.to_dict()
    product.raw_payload["platform_brand"] = product.brand
    product.raw_payload["display"] = display
    product.raw_payload["operating_system"] = operating_system
    return product


def build_from_listing(
    *,
    retailer_code: str,
    country_code: str,
    currency: str,
    sku: str,
    source_url: str,
    title: Optional[str],
    price_text: Optional[str],
    list_price_text: Optional[str],
    promo_text: Optional[str],
    category_raw: Optional[str],
    detail_page_status: str,
    extra_raw: Optional[dict[str, Any]] = None,
):
    """Normalize a product using listing-level evidence only."""
    store = ProvenanceStore()
    card = dict(extra_raw or {})
    card.update(
        {
            "title": title,
            "price_text": price_text,
            "list_price_text": list_price_text,
            "promo_text": promo_text,
            "sku": sku,
            "source_url": source_url,
            "href": source_url,
        }
    )
    apply_listing_card(store, card)
    apply_title_heuristics(store, store.get_value("title") or title)

    classified = classify_mercadolibre_product(
        title=store.get_value("title") or title,
        category_raw=category_raw,
        specs=store.get_value("specs_raw") or {},
        discovery_name=str((extra_raw or {}).get("discovery_name") or "") or None,
    )
    block_reason = map_block_reason(detail_page_status)
    evidence = listing_only_evidence(reason=block_reason)
    listing_audit = {
        "title": store.get_value("title") or title,
        "tile_text": (extra_raw or {}).get("tile_text"),
        "badge_texts": list((extra_raw or {}).get("badge_texts") or [])
        + list((extra_raw or {}).get("badge_alts") or []),
        "selectors_used": ["listing_card"],
        "source_url": source_url,
        "available": True,
    }
    return _build_product(
        retailer_code=retailer_code,
        country_code=country_code,
        currency=currency,
        source_url=source_url,
        category_raw=category_raw,
        store=store,
        fallback_sku=sku,
        fallback_title=title,
        extra_raw=extra_raw,
        detail_page_status=detail_page_status,
        evidence_bundle=evidence,
        classified=classified,
        badge_signals={
            "badge_texts": listing_audit["badge_texts"],
            "img_alts": list((extra_raw or {}).get("badge_alts") or []),
        },
        specs_inspected=False,
        pdp_accessible=False,
        listing_audit=listing_audit,
    )


async def parse_product_page(
    page,
    *,
    retailer_code: str,
    country_code: str,
    currency: str,
    fallback_sku: str,
    fallback_title: Optional[str] = None,
    fallback_price: Optional[str] = None,
    fallback_list_price: Optional[str] = None,
    fallback_promo: Optional[str] = None,
    category_raw: Optional[str] = None,
    listing_raw: Optional[dict[str, Any]] = None,
    network_payloads: Optional[list[Any]] = None,
) -> Any:
    html = await page.content()
    title_txt = await page.title()
    url = page.url
    if is_account_verification(html, url) or is_account_verification(title_txt, url):
        raise RuntimeError("mercadolibre_account_verification")
    if is_bot_challenge(html) or is_bot_challenge(title_txt):
        raise RuntimeError("mercadolibre_bot_challenge")

    store = ProvenanceStore()

    try:
        dom_payload = await page.evaluate(DOM_EXTRACT_JS)
    except Exception:  # noqa: BLE001
        dom_payload = {}
    if isinstance(dom_payload, dict):
        apply_dom_payload(store, dom_payload)
    else:
        store.mark_layer("visible_dom")
        store.mark_layer("aria_alt_title")
        dom_payload = {}

    try:
        embedded = await page.evaluate(EMBEDDED_EXTRACT_JS)
    except Exception:  # noqa: BLE001
        embedded = {}
    json_ld = {}
    if isinstance(embedded, dict):
        json_ld = parse_json_ld_products(list(embedded.get("json_ld_scripts") or []))
        apply_json_ld(store, json_ld)
        if embedded.get("state") is not None:
            apply_embedded_or_network(
                store,
                embedded.get("state"),
                source=SOURCE_EMBEDDED_JSON,
                method=METHOD_EMBEDDED_STATE,
                layer_name="embedded_json",
            )
        else:
            store.mark_layer("embedded_json")
        for blob in embedded.get("blobs") or []:
            parsed = parse_embedded_script_text(blob) if isinstance(blob, str) else blob
            if parsed is not None:
                apply_embedded_or_network(
                    store,
                    parsed,
                    source=SOURCE_EMBEDDED_JSON,
                    method=METHOD_EMBEDDED_STATE,
                    layer_name="embedded_json",
                )
    else:
        store.mark_layer("json_ld")
        store.mark_layer("embedded_json")

    store.mark_layer("network_json")
    for payload in network_payloads or []:
        apply_embedded_or_network(
            store,
            payload,
            source=SOURCE_NETWORK,
            method=METHOD_NETWORK_JSON,
            layer_name="network_json",
        )

    listing_card = dict(listing_raw or {})
    listing_card.setdefault("title", fallback_title)
    listing_card.setdefault("price_text", fallback_price)
    listing_card.setdefault("list_price_text", fallback_list_price)
    listing_card.setdefault("promo_text", fallback_promo)
    listing_card.setdefault("sku", fallback_sku)
    listing_card.setdefault("href", url)
    apply_listing_card(store, listing_card)
    apply_title_heuristics(store, store.get_value("title") or fallback_title)

    specs_raw = store.get_value("specs_raw") or {}
    specs_from_structured = bool(specs_raw) and any(
        obs.extraction_method
        in {
            "DOM_text",
            "json_ld",
            "embedded_state",
            "network_json",
        }
        for name, obs in store.fields.items()
        if name in {"processor", "ram", "storage", "gpu", "display", "operating_system", "specs_raw"}
    )
    # Spec table / structured attributes count as inspected specs; title regex does not.
    specs_inspected = specs_from_structured
    if not specs_inspected:
        # DOM spec rows may live only in specs_raw from DOM_text.
        spec_obs = store.fields.get("specs_raw")
        if spec_obs and spec_obs.extraction_method == "DOM_text" and spec_obs.value:
            specs_inspected = True
        elif spec_obs and spec_obs.extraction_method in {
            "json_ld",
            "embedded_state",
            "network_json",
        }:
            specs_inspected = True

    evidence = product_page_evidence(
        specs_available=specs_inspected,
        specs_reason=REASON_SPECS_AVAILABLE if specs_inspected else REASON_SPECS_NOT_FOUND,
        badges_inspected=True,
        media_inspected=True,
    )
    classified = classify_mercadolibre_product(
        title=store.get_value("title") or fallback_title,
        category_raw=category_raw,
        specs=specs_raw if isinstance(specs_raw, dict) else {},
    )
    badge_signals = collect_badge_signals(dom_payload if isinstance(dom_payload, dict) else {})
    listing_audit = {
        "title": fallback_title,
        "tile_text": (listing_raw or {}).get("tile_text"),
        "badge_texts": list((listing_raw or {}).get("badge_texts") or [])
        + list((listing_raw or {}).get("badge_alts") or []),
        "selectors_used": ["listing_card"],
        "source_url": url,
        "available": True,
    }
    extra = {
        "json_ld": bool(json_ld),
        "embedded_json": "embedded_json" in store.layers_attempted,
        "network_json": bool(network_payloads),
        "layers_attempted": store.layers_attempted,
        "pdp_audit": {
            "badges_inspected": True,
            "media_inspected": True,
            "badge_texts": badge_signals.get("badge_texts") or [],
            "brand_media_signals": (badge_signals.get("img_alts") or [])
            + (badge_signals.get("aria_labels") or []),
            "oem_media_signals": (badge_signals.get("img_alts") or [])
            + (badge_signals.get("img_titles") or []),
            "specs_available": specs_inspected,
            "access_reason": REASON_OK if specs_inspected else REASON_SPECS_NOT_FOUND,
            "selectors_used": list(PRODUCT_TITLE_SELECTORS)
            + list(PRODUCT_PRICE_SELECTORS)
            + list(SPEC_ROW_SELECTORS),
        },
    }
    return _build_product(
        retailer_code=retailer_code,
        country_code=country_code,
        currency=currency,
        source_url=url.split("?")[0].split("#")[0],
        category_raw=category_raw,
        store=store,
        fallback_sku=fallback_sku,
        fallback_title=fallback_title,
        extra_raw=extra,
        detail_page_status="ok",
        evidence_bundle=evidence,
        classified=classified,
        badge_signals=badge_signals,
        specs_inspected=specs_inspected,
        pdp_accessible=True,
        listing_audit=listing_audit,
    )
