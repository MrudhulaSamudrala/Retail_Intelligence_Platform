"""Deterministic normalization for brand, OEM, product type, price, availability."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from collector.classification import (
    UNKNOWN,
    classify_product,
    detect_brand,
    detect_oem,
    is_discrete_gpu_product,
    is_system_computer_title,
)
from collector.config_loader import load_product_types

# Re-export classification helpers for existing imports.
__all__ = [
    "UNKNOWN",
    "NormalizedProduct",
    "detect_brand",
    "detect_oem",
    "detect_product_type",
    "classify_product_type",
    "non_computing_exclusion",
    "title_is_irrelevant",
    "title_has_irrelevant_signal",
    "parse_price",
    "normalize_availability",
    "build_normalized_product",
]


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


# Spec keys that describe CPU *class* (Desktop vs Mobile), not the product sold.
_TYPE_NOISE_SPEC_KEYS = frozenset(
    {
        "processors type",
        "processor type",
        "cpu type",
        "core type",
        "stream processors",
        "stream processor",
    }
)

# Furniture / stands / digitizers — must win over "workstation"/"cpu"/"tablet" aliases.
_FURNITURE_RE = re.compile(
    r"\b("
    r"desks?|cubicles?|escrivaninha|mesa\s+gamer|"
    r"standing\s+desk|office\s+desk|computer\s+desk|gaming\s+desk|"
    r"farmhouse.{0,20}\bdesk"
    r")\b",
    re.I,
)
_ACCESSORY_STAND_RE = re.compile(
    r"\b("
    r"cpu\s+holders?|cpu\s+stands?|tower\s+stands?|pc\s+stands?|"
    r"pc[- ]mounts?|computer\s+tower\s+stands?|under\s+desk\s+pc|"
    r"monitor\s+stands?|laptop\s+stands?|pc\s+carts?"
    r")\b",
    re.I,
)
_DRAWING_TABLET_RE = re.compile(
    r"\b("
    r"drawing\s+tablets?|graphic\s+tablets?|graphics\s+tablets?|"
    r"drawing\s+pads?|digitizers?|pen\s+tablets?|mesa\s+digitalizadora"
    r")\b",
    re.I,
)
_STANDALONE_CPU_RE = re.compile(
    r"\b("
    r"desktop\s+processor|processor\s+only|boxed\s+processor|"
    r"cpu\s+processor|desktop\s+cpu|tray\s+processor|"
    r"socket\s+(?:am[45]|am4|am5|lga\s?\d{3,4}|strx?5|sp[35])|"
    r"ryzen\s+[3579]\s+\d{4,5}"
    r")\b",
    re.I,
)
_STRATUM_HINTS = frozenset(
    {"notebook", "desktop", "workstation", "tablet", "cpu", "gpu"}
)


def _match_type_in_blob(blob: str, types: list) -> Optional[str]:
    """Longest alias wins so 'desktop processor' is cpu, not desktop."""
    if not blob.strip():
        return None
    lowered = blob.lower()
    best_code: Optional[str] = None
    best_len = 0
    for item in types:
        for alias in item.get("aliases", []):
            token = str(alias).lower().strip()
            if token and token in lowered and len(token) > best_len:
                best_len = len(token)
                best_code = item["code"]
    return best_code


def non_computing_exclusion(title: Optional[str]) -> Optional[tuple[str, str]]:
    """Return (product_type_reason, exclusion_reason) for furniture/accessories."""
    if not title or not title.strip():
        return None
    if _DRAWING_TABLET_RE.search(title):
        return "furniture_hard_negative", "NON_COMPUTING_PRODUCT"
    if _ACCESSORY_STAND_RE.search(title):
        return "furniture_hard_negative", "ACCESSORY"
    if _FURNITURE_RE.search(title):
        return "furniture_hard_negative", "FURNITURE"
    return None


def _looks_like_standalone_cpu(
    *,
    title: Optional[str],
    specs: Optional[dict[str, str]],
    category_raw: Optional[str],
) -> bool:
    if is_system_computer_title(title):
        return False
    if non_computing_exclusion(title):
        return False
    parts = [title or ""]
    if specs:
        parts.extend(f"{k}: {v}" for k, v in specs.items() if v)
    if category_raw and not _is_discovery_slug(category_raw):
        parts.append(category_raw)
    blob = " ".join(parts)
    if _STANDALONE_CPU_RE.search(blob):
        return True
    cat = (category_raw or "").strip().lower()
    if cat in {"cpu", "processor", "processors"} and not is_system_computer_title(title):
        return True
    return False


def title_has_irrelevant_signal(title: Optional[str]) -> bool:
    """True when title contains a configured hard-negative phrase (TV, bike, …)."""
    if not title or not title.strip():
        return False
    title_l = title.lower()
    signals = load_product_types().get("irrelevant_title_signals") or []
    for signal in signals:
        token = str(signal).lower().strip()
        if token and token in title_l:
            return True
    if re.search(r"\btvs?\b", title_l):
        return True
    return False


def title_is_irrelevant(title: Optional[str]) -> bool:
    """True when title is non-computing junk (hard negatives beat type aliases)."""
    if not title or not title.strip():
        return False
    if title_has_irrelevant_signal(title):
        return True
    return False


def _is_discovery_slug(category_raw: Optional[str]) -> bool:
    """Ofertas / discovery / stratum names must not drive product_type alone."""
    if not category_raw:
        return False
    c = category_raw.strip().lower()
    return (
        c.endswith("_ofertas")
        or c.endswith("_search")
        or "ofertas" in c
        or c.startswith("search:")
        or c in _STRATUM_HINTS
    )


def evidence_category_raw(category_raw: Optional[str]) -> Optional[str]:
    """Category used for classification. Stratum/query slugs are not evidence."""
    if not category_raw or _is_discovery_slug(category_raw):
        return None
    return category_raw


def _title_type_blob(title: Optional[str], specs: Optional[dict[str, str]]) -> str:
    """Title plus non-noisy specs. Never treat 'Processors Type=Desktop' as product type."""
    parts = [title or ""]
    if specs:
        for key, value in specs.items():
            if not value:
                continue
            if str(key).strip().lower() in _TYPE_NOISE_SPEC_KEYS:
                continue
            parts.append(str(value))
    return " ".join(parts)


def classify_product_type(
    *,
    category_raw: Optional[str] = None,
    title: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
) -> tuple[str, str]:
    """Classify product type from the product being sold. Returns (code, reason)."""
    cfg = load_product_types()
    types = cfg.get("product_types", [])

    excluded = non_computing_exclusion(title)
    if excluded:
        return "other", excluded[0]

    if title_has_irrelevant_signal(title):
        return UNKNOWN, "hard_negative_title"

    if is_system_computer_title(title):
        title_only = _match_type_in_blob(title or "", types)
        if title_only in {"cpu", "gpu"}:
            title_only = None
        if title_only in {"notebook", "desktop", "workstation", "tablet"}:
            return title_only, f"title_or_specs_alias:{title_only}"
        if re.search(r"\b(laptop|notebook|chromebook|macbook|ultrabook)\b", title or "", re.I):
            return "notebook", "title_or_specs_alias:notebook"
        return "desktop", "title_or_specs_alias:desktop"

    if _looks_like_standalone_cpu(
        title=title, specs=specs, category_raw=category_raw
    ):
        return "cpu", "standalone_cpu_evidence"

    if is_discrete_gpu_product(title=title, gpu=None) or (
        specs
        and is_discrete_gpu_product(
            title=title,
            gpu=" ".join(str(v) for v in specs.values() if v),
        )
    ):
        return "gpu", "graphics_card_title"

    matched = _match_type_in_blob(_title_type_blob(title, specs), types)
    if matched:
        return matched, f"title_or_specs_alias:{matched}"

    # Android slate evidence in the title (not a stratum hint).
    if title and re.search(r"\bandroid\b", title, re.I) and re.search(
        r'(\d+\s*(?:["”]|inch)|tablet|2-in-1|2\s*in\s*1)',
        title,
        re.I,
    ):
        return "tablet", "title_or_specs_alias:tablet"

    if category_raw and not _is_discovery_slug(category_raw):
        matched = _match_type_in_blob(category_raw, types)
        if matched:
            return matched, f"category_alias:{matched}"

    return UNKNOWN, "insufficient_type_evidence"


def detect_product_type(
    *,
    category_raw: Optional[str] = None,
    title: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
) -> str:
    """Classify product type from evidence (code only)."""
    code, _reason = classify_product_type(
        category_raw=category_raw, title=title, specs=specs
    )
    return code


def parse_price(text: Optional[str]) -> Optional[Decimal]:
    """Parse retailer price text into Decimal.

    Supports US-style ``1,234.56`` / ``$1234.56`` and Brazilian
    ``R$ 3.999,90`` / ``3999,90`` forms. Does not invent values.
    """
    if not text:
        return None
    cleaned = (
        text.replace("R$", "")
        .replace("r$", "")
        .replace("$", "")
        .replace("\xa0", " ")
        .strip()
    )
    cleaned = re.sub(r"[^\d.,]", "", cleaned)
    if not cleaned:
        return None

    # Brazilian: thousands '.', decimal ','
    if re.search(r"\d{1,3}(\.\d{3})+(,\d+)?$", cleaned) or (
        "," in cleaned and "." in cleaned and cleaned.rfind(",") > cleaned.rfind(".")
    ):
        normalized = cleaned.replace(".", "").replace(",", ".")
    # Decimal comma only (e.g. 3999,90)
    elif "," in cleaned and "." not in cleaned:
        normalized = cleaned.replace(",", ".")
    else:
        # US / plain: strip thousands commas
        normalized = cleaned.replace(",", "")

    match = re.search(r"(\d+(?:\.\d+)?)", normalized)
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
    if any(
        x in value
        for x in (
            "out of stock",
            "sold out",
            "unavailable",
            "esgotado",
            "indisponível",
            "indisponivel",
        )
    ):
        return "out_of_stock"
    if any(
        x in value
        for x in (
            "in stock",
            "add to cart",
            "ship",
            "available",
            "estoque",
            "disponível",
            "disponivel",
            "comprar",
        )
    ):
        return "in_stock"
    if "limited" in value or "últimas" in value or "ultimas" in value:
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
    manufacturer: Optional[str] = None,
    description: Optional[str] = None,
    raw_payload: Optional[dict[str, Any]] = None,
) -> NormalizedProduct:
    specs = specs or {}
    product_type, product_type_reason = classify_product_type(
        category_raw=category_raw, title=title, specs=specs
    )

    # Brand/OEM classification is independent and lives in collector.classification.
    classified = classify_product(
        title=title,
        processor=processor,
        manufacturer=manufacturer,
        specifications=specs,
        description=description,
        product_type=product_type,
        gpu=gpu,
    )
    brand = classified.brand
    oem = classified.oem

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
            "brand_reason": classified.brand_reason,
            "oem_reason": classified.oem_reason,
            "product_type_reason": product_type_reason,
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
