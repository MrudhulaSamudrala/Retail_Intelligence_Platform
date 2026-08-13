"""Map official Mercado Libre API JSON to field observations.

Only fields actually present in the JSON are emitted. Attribute IDs come from
the documented items/products attribute objects (id, name, value_name).
"""

from __future__ import annotations

from typing import Any, Optional

from collector.retailers.mercadolibre.field_evidence import (
    METHOD_API,
    SOURCE_API,
    ProvenanceStore,
    observation,
)
from collector.retailers.mercadolibre.pt_labels import english_spec_key

# Official attribute ids observed in /items and /products datasheets.
_ATTR_FIELD_MAP = {
    "BRAND": "oem_raw",
    "MODEL": "model",
    "LINE": "model",
    "GTIN": "gtin",
    "EAN": "gtin",
    "UPC": "gtin",
    "MPN": "mpn",
    "PART_NUMBER": "mpn",
    "PROCESSOR_MODEL": "processor",
    "PROCESSOR_BRAND": "platform_raw",
    "CPU_MODEL": "processor",
    "RAM": "ram",
    "RAM_MEMORY": "ram",
    "RAM_SIZE": "ram",
    "MEMORY": "ram",
    "STORAGE_CAPACITY": "storage",
    "SSD_CAPACITY": "storage",
    "HDD_CAPACITY": "storage",
    "HARD_DRIVE_CAPACITY": "storage",
    "GRAPHICS_PROCESSOR": "gpu",
    "GPU_MODEL": "gpu",
    "GRAPHIC_PROCESSOR": "gpu",
    "DISPLAY_SIZE": "display",
    "SCREEN_SIZE": "display",
    "DISPLAY": "display",
    "OPERATING_SYSTEM": "operating_system",
    "OS": "operating_system",
}


def _text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def apply_api_payload(store: ProvenanceStore, payload: dict[str, Any], *, endpoint: str) -> None:
    store.mark_layer("api")
    if not payload:
        return
    title = _text(payload.get("title") or payload.get("name"))
    if title:
        store.set_if_empty(
            "title",
            observation(title, source=SOURCE_API, extraction_method=METHOD_API, raw=title),
        )
    sku = _text(payload.get("id") or payload.get("catalog_product_id"))
    if sku:
        store.set_if_empty(
            "api_id",
            observation(sku, source=SOURCE_API, extraction_method=METHOD_API),
        )
    price = _text(payload.get("price") or payload.get("sale_price"))
    currency = _text(payload.get("currency_id")) or "BRL"
    if price:
        store.set_if_empty(
            "price",
            observation(price, source=SOURCE_API, extraction_method=METHOD_API, currency=currency),
        )
    original = _text(payload.get("original_price") or payload.get("base_price"))
    if original:
        store.set_if_empty(
            "list_price",
            observation(
                original, source=SOURCE_API, extraction_method=METHOD_API, currency=currency
            ),
        )
    qty = payload.get("available_quantity")
    if qty is not None:
        avail = "in_stock" if isinstance(qty, (int, float)) and qty > 0 else "out_of_stock"
        store.set_if_empty(
            "availability",
            observation(avail, source=SOURCE_API, extraction_method=METHOD_API, raw=str(qty)),
        )
    category = _text(payload.get("category_id") or payload.get("domain_id"))
    if category:
        store.set_if_empty(
            "category_id",
            observation(category, source=SOURCE_API, extraction_method=METHOD_API),
        )
    seller = payload.get("seller_id")
    if seller is not None:
        store.set_if_empty(
            "seller_id",
            observation(str(seller), source=SOURCE_API, extraction_method=METHOD_API),
        )
    pictures = payload.get("pictures")
    if isinstance(pictures, list) and pictures:
        store.set_if_empty(
            "pictures_count",
            observation(len(pictures), source=SOURCE_API, extraction_method=METHOD_API),
        )
    variations = payload.get("variations")
    if isinstance(variations, list) and variations:
        store.set_if_empty(
            "variations_count",
            observation(len(variations), source=SOURCE_API, extraction_method=METHOD_API),
        )
    winner = payload.get("buy_box_winner")
    if isinstance(winner, dict) and winner.get("price") is not None:
        store.set_if_empty(
            "buy_box_price",
            observation(
                str(winner.get("price")),
                source=SOURCE_API,
                extraction_method=METHOD_API,
                currency=currency,
            ),
        )

    specs: dict[str, str] = {}
    attrs = payload.get("attributes")
    if isinstance(attrs, list):
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            attr_id = str(attr.get("id") or "").upper()
            name = _text(attr.get("name")) or attr_id
            value = _text(attr.get("value_name"))
            if not value and isinstance(attr.get("values"), list) and attr["values"]:
                first = attr["values"][0]
                if isinstance(first, dict):
                    value = _text(first.get("name") or first.get("value_name"))
            if not value:
                continue
            specs[name] = value
            field_name = _ATTR_FIELD_MAP.get(attr_id)
            if not field_name and name:
                en = english_spec_key(name)
                if en == "brand":
                    field_name = "oem_raw"
                elif en:
                    field_name = en
            if field_name:
                store.set_if_empty(
                    field_name,
                    observation(value, source=SOURCE_API, extraction_method=METHOD_API, raw=name),
                )
    if specs:
        existing = store.get_value("specs_raw") or {}
        merged = dict(existing) if isinstance(existing, dict) else {}
        for key, value in specs.items():
            merged.setdefault(key, value)
        store.fields["specs_raw"] = observation(
            merged, source=SOURCE_API, extraction_method=METHOD_API
        )
    store.set_if_empty(
        "api_endpoint",
        observation(endpoint, source=SOURCE_API, extraction_method=METHOD_API),
    )
