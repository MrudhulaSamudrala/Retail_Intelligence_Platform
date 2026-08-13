"""Optional official-API enrichment of an already-collected NormalizedProduct."""

from __future__ import annotations

import logging
from typing import Optional

from collector.evidence import (
    COMPLETE,
    PARTIAL,
    UNKNOWN,
    EvidenceBundle,
    REASON_API_DISABLED,
    REASON_SPECS_AVAILABLE,
    SURFACE_API,
    SURFACE_SPECS,
)
from collector.normalize import NormalizedProduct, build_normalized_product
from collector.retailers.mercadolibre.api.client import MercadoLibreApiClient, STATUS_OK
from collector.retailers.mercadolibre.api.config import load_api_config
from collector.retailers.mercadolibre.api.merge import merge_stores
from collector.retailers.mercadolibre.api.normalize import apply_api_payload
from collector.retailers.mercadolibre.classification import classify_mercadolibre_product
from collector.retailers.mercadolibre.field_evidence import ProvenanceStore
from collector.retailers.mercadolibre.product_page import SPEC_ALIASES, pick_spec
from collector.retailers.mercadolibre.pt_labels import normalize_spec_map

logger = logging.getLogger("collector.mercadolibre.api")

_REQUIRED = [
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


def enrich_product(
    product: NormalizedProduct,
    *,
    client: Optional[MercadoLibreApiClient] = None,
) -> NormalizedProduct:
    """Attach official API evidence. Never raises into the collection loop."""
    raw = dict(product.raw_payload or {})
    cfg = load_api_config() if client is None else client.config
    if client is None:
        client = MercadoLibreApiClient(cfg)
    if not cfg.enabled:
        raw["api_status"] = REASON_API_DISABLED
        bundle = EvidenceBundle.from_dict(raw.get("evidence"))
        bundle.set_surface(
            SURFACE_API,
            status=UNKNOWN,
            reason=REASON_API_DISABLED,
            source="api",
            notes="credentials_not_configured",
        )
        raw["evidence"] = bundle.to_dict()
        product.raw_payload = raw
        return product

    try:
        lookup = client.lookup(product.retailer_sku, source_url=product.source_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mercadolibre_api_lookup_failed",
            extra={
                "event": "mercadolibre_api_lookup_failed",
                "sku": product.retailer_sku,
                "error": str(exc),
            },
        )
        raw["api_status"] = "API_UNAVAILABLE"
        raw["api_error"] = str(exc)
        product.raw_payload = raw
        return product

    raw["api_status"] = lookup.status
    raw["api_lookup"] = lookup.to_dict()
    bundle = EvidenceBundle.from_dict(raw.get("evidence"))
    api_surface_status = COMPLETE if lookup.status == STATUS_OK else UNKNOWN
    if lookup.status in {"API_UNAVAILABLE", "API_AUTH_FAILED", "API_RATE_LIMITED"}:
        api_surface_status = UNKNOWN
    bundle.set_surface(
        SURFACE_API,
        status=api_surface_status if lookup.status == STATUS_OK else UNKNOWN,
        reason=lookup.status if lookup.status != STATUS_OK else "OK",
        source="api",
    )

    if lookup.status != STATUS_OK:
        raw["evidence"] = bundle.to_dict()
        product.raw_payload = raw
        return product

    base = ProvenanceStore.from_dict(raw.get("field_provenance"))
    api_store = ProvenanceStore()
    for payload in lookup.payloads():
        endpoint = "products" if payload.get("domain_id") or payload.get("name") and not payload.get("seller_id") else "items"
        if lookup.product and lookup.product.payload is payload:
            endpoint = lookup.product.endpoint or "/products"
        elif lookup.item and lookup.item.payload is payload:
            endpoint = lookup.item.endpoint or "/items"
        apply_api_payload(api_store, payload, endpoint=endpoint)
    merged = merge_stores(base, api_store)

    specs_raw = merged.get_value("specs_raw") or raw.get("specs") or {}
    if not isinstance(specs_raw, dict):
        specs_raw = {}
    specs_normalized = normalize_spec_map(specs_raw)
    processor = merged.get_value("processor") or pick_spec(specs_raw, SPEC_ALIASES["processor"]) or product.processor
    gpu = merged.get_value("gpu") or pick_spec(specs_raw, SPEC_ALIASES["gpu"]) or product.gpu
    ram = merged.get_value("ram") or merged.get_value("memory") or pick_spec(
        specs_raw, SPEC_ALIASES["ram"]
    ) or product.ram
    storage = merged.get_value("storage") or pick_spec(specs_raw, SPEC_ALIASES["storage"]) or product.storage
    display = merged.get_value("display") or raw.get("display")
    operating_system = merged.get_value("operating_system") or raw.get("operating_system")
    classified = classify_mercadolibre_product(
        title=str(merged.get_value("title") or product.title or ""),
        category_raw=product.category_raw,
        specs=specs_raw,
        discovery_name=str(raw.get("discovery_name") or "") or None,
    )
    api_specs = bool(
        any(
            merged.fields.get(name) and merged.fields[name].source == "api"
            for name in ("processor", "ram", "storage", "gpu", "display", "operating_system", "gtin")
        )
        or (isinstance(specs_raw, dict) and len(specs_raw) >= 3 and "api" in merged.layers_attempted)
    )
    if api_specs:
        bundle.set_surface(
            SURFACE_SPECS,
            status=COMPLETE,
            reason=REASON_SPECS_AVAILABLE,
            source="api",
            notes="official_api_attributes",
        )

    price_text = merged.get_value("price")
    list_price_text = merged.get_value("list_price")
    rebuilt = build_normalized_product(
        retailer_code=product.retailer_code,
        country_code=product.country_code,
        currency=product.currency or "BRL",
        retailer_sku=product.retailer_sku,
        source_url=product.source_url,
        title=str(merged.get_value("title") or product.title or ""),
        category_raw=product.category_raw,
        price_text=str(price_text) if price_text is not None else (
            str(product.price_amount) if product.price_amount is not None else None
        ),
        list_price_text=str(list_price_text) if list_price_text is not None else (
            str(product.list_price) if product.list_price is not None else None
        ),
        availability_text=merged.get_value("availability") or product.availability,
        promo_text=merged.get_value("promo_text") or product.promo_text,
        processor=processor,
        gpu=gpu,
        ram=ram,
        storage=storage,
        specs=specs_raw,
        manufacturer=merged.get_value("oem_raw"),
        raw_payload=raw,
    )
    rebuilt.product_type = classified.product_type or product.product_type
    rebuilt.raw_payload["classification"] = classified.to_dict()
    rebuilt.raw_payload["evidence"] = bundle.to_dict()
    rebuilt.raw_payload["field_provenance"] = merged.to_dict()
    rebuilt.raw_payload["specs_normalized"] = specs_normalized
    rebuilt.raw_payload["specs_raw_labels"] = specs_raw
    rebuilt.raw_payload["identity"] = {
        "gtin": merged.get_value("gtin"),
        "mpn": merged.get_value("mpn"),
        "oem_raw": merged.get_value("oem_raw"),
        "model": merged.get_value("model"),
        "api_id": merged.get_value("api_id"),
    }
    rebuilt.raw_payload["display"] = display
    rebuilt.raw_payload["operating_system"] = operating_system
    rebuilt.raw_payload["platform_brand"] = rebuilt.brand
    rebuilt.raw_payload["gaming_relevance"] = "gaming" if classified.gaming else raw.get(
        "gaming_relevance"
    ) or "non_gaming"
    rebuilt.raw_payload["unknown_fields"] = merged.unknown_fields(_REQUIRED)
    rebuilt.raw_payload["api_status"] = lookup.status
    rebuilt.raw_payload["api_lookup"] = lookup.to_dict()
    rebuilt.raw_payload["field_conflicts"] = merged.conflicts
    price_obs = merged.fields.get("price")
    if price_obs:
        rebuilt.raw_payload["price_source"] = price_obs.source
        rebuilt.raw_payload["price_extraction_method"] = price_obs.extraction_method
    completeness = bundle.overall_status
    if rebuilt.raw_payload.get("unknown_fields"):
        completeness = PARTIAL
    rebuilt.raw_payload["evidence_completeness"] = completeness
    # Never change identity key.
    rebuilt.retailer_sku = product.retailer_sku
    return rebuilt
