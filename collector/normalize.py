"""Deterministic normalization for brand, OEM, product type, price, availability."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from collector.config_loader import load_brands, load_oems, load_product_types

UNKNOWN = "UNKNOWN"


@dataclass
class NormalizedProduct:
    """Retailer-agnostic product payload ready for persistence."""

    retailer_code: str
    country_code: str
    retailer_sku: str
    source_url: str
    title: Optional[str] = None
    brand: Optional[str] = None
    oem: Optional[str] = None
    product_type: Optional[str] = None
    category_raw: Optional[str] = None
    price_amount: Optional[Decimal] = None
    list_price: Optional[Decimal] = None
    currency: Optional[str] = None
    availability: Optional[str] = None
    is_on_promotion: bool = False
    promo_text: Optional[str] = None
    promo_type: Optional[str] = None
    discount_amount: Optional[Decimal] = None
    discount_pct: Optional[Decimal] = None
    processor: Optional[str] = None
    gpu: Optional[str] = None
    ram: Optional[str] = None
    storage: Optional[str] = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


def _compile_aliases(items: list[dict[str, Any]], key: str = "aliases") -> list[tuple[str, list[str]]]:
    compiled: list[tuple[str, list[str]]] = []
    for item in items:
        name = item["name"]
        aliases = [a.lower() for a in item.get(key, [])]
        families = [f.lower() for f in item.get("processor_families", [])]
        compiled.append((name, sorted(set(aliases + families), key=len, reverse=True)))
    return compiled


def _alias_matches(alias: str, blob: str) -> bool:
    """Match aliases in text; use word boundaries for short tokens (e.g. hp vs hdmi)."""
    alias = alias.lower().strip()
    if not alias or not blob:
        return False
    if len(alias) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", blob) is not None
    return alias in blob


def detect_brand(*texts: Optional[str]) -> str:
    """Brand hierarchy approximated over concatenated evidence strings."""
    brands = load_brands().get("brands", [])
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return UNKNOWN

    # Prefer explicit manufacturer aliases, then processor families (already merged).
    for name, aliases in _compile_aliases(brands):
        for alias in aliases:
            if _alias_matches(alias, blob):
                return name
    return UNKNOWN


def detect_oem(*texts: Optional[str], product_type: Optional[str] = None) -> Optional[str]:
    if product_type in {"cpu", "gpu"}:
        return None

    oems = load_oems().get("oems", [])
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    for item in oems:
        for alias in sorted(item.get("aliases", []), key=len, reverse=True):
            if _alias_matches(alias.lower(), blob):
                return item["name"]
    return None


def detect_product_type(
    *,
    category_raw: Optional[str] = None,
    title: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
) -> str:
    cfg = load_product_types()
    types = cfg.get("product_types", [])
    excluded = [e.lower() for e in cfg.get("excluded_categories", [])]

    evidence_parts = [category_raw or "", title or ""]
    if specs:
        evidence_parts.extend(specs.values())
    blob = " ".join(evidence_parts).lower()
    if not blob.strip():
        return UNKNOWN

    for excl in excluded:
        # Only treat as excluded when category strongly indicates accessory-only.
        if category_raw and excl in category_raw.lower() and not any(
            t["code"] in (category_raw or "").lower() for t in types
        ):
            # Soft signal — still try positive matches below.
            pass

    for item in types:
        for alias in sorted(item.get("aliases", []), key=len, reverse=True):
            if alias.lower() in blob:
                return item["code"]
    return UNKNOWN


def parse_price(text: Optional[str]) -> Optional[Decimal]:
    if not text:
        return None
    cleaned = text.replace(",", "").replace("$", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def normalize_availability(text: Optional[str]) -> str:
    if not text:
        return "unknown"
    value = text.lower()
    if any(x in value for x in ("out of stock", "sold out", "unavailable")):
        return "out_of_stock"
    if any(x in value for x in ("in stock", "add to cart", "ship", "available")):
        return "in_stock"
    if "limited" in value:
        return "limited"
    return "unknown"


def build_normalized_product(
    *,
    retailer_code: str,
    country_code: str,
    currency: str,
    retailer_sku: str,
    source_url: str,
    title: Optional[str],
    category_raw: Optional[str] = None,
    price_text: Optional[str] = None,
    list_price_text: Optional[str] = None,
    availability_text: Optional[str] = None,
    promo_text: Optional[str] = None,
    processor: Optional[str] = None,
    gpu: Optional[str] = None,
    ram: Optional[str] = None,
    storage: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
    raw_payload: Optional[dict[str, Any]] = None,
) -> NormalizedProduct:
    specs = specs or {}
    product_type = detect_product_type(category_raw=category_raw, title=title, specs=specs)
    brand = detect_brand(title, processor, gpu, category_raw, " ".join(specs.values()))
    oem = detect_oem(title, category_raw, " ".join(specs.values()), product_type=product_type)

    price_amount = parse_price(price_text)
    list_price = parse_price(list_price_text)
    discount_amount = None
    discount_pct = None
    is_on_promotion = bool(promo_text) or (
        list_price is not None and price_amount is not None and list_price > price_amount
    )
    if list_price is not None and price_amount is not None and list_price > price_amount:
        discount_amount = list_price - price_amount
        try:
            discount_pct = (discount_amount / list_price) * Decimal("100")
        except InvalidOperation:
            discount_pct = None

    payload = dict(raw_payload or {})
    payload.update(
        {
            "processor": processor,
            "gpu": gpu,
            "ram": ram,
            "storage": storage,
            "specs": specs,
        }
    )

    return NormalizedProduct(
        retailer_code=retailer_code,
        country_code=country_code,
        retailer_sku=retailer_sku,
        source_url=source_url,
        title=title,
        brand=brand,
        oem=oem,
        product_type=product_type,
        category_raw=category_raw,
        price_amount=price_amount,
        list_price=list_price,
        currency=currency,
        availability=normalize_availability(availability_text),
        is_on_promotion=is_on_promotion,
        promo_text=promo_text,
        promo_type="sale" if is_on_promotion else None,
        discount_amount=discount_amount,
        discount_pct=discount_pct,
        processor=processor,
        gpu=gpu,
        ram=ram,
        storage=storage,
        raw_payload=payload,
    )
