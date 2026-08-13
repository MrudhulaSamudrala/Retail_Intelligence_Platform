"""Mercado Libre product-page / listing enrichment parsing."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from collector.normalize import build_normalized_product
from collector.retailers.mercadolibre.listing import extract_mlb_id
from collector.retailers.mercadolibre.selectors import (
    ACCOUNT_VERIFICATION_MARKERS,
    BOT_CHALLENGE_MARKERS,
    PRODUCT_AVAILABILITY_SELECTORS,
    PRODUCT_PRICE_SELECTORS,
    PRODUCT_TITLE_SELECTORS,
    PRODUCT_WAS_PRICE_SELECTORS,
    SPEC_ROW_SELECTORS,
)

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
}


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


def pick_spec(specs: dict[str, str], aliases: list[str]) -> Optional[str]:
    lowered = {k.lower().strip(): v.strip() for k, v in specs.items() if v}
    for alias in aliases:
        if alias in lowered and lowered[alias]:
            return lowered[alias]
    for key, value in lowered.items():
        for alias in aliases:
            if alias in key and value:
                return value
    return None


def specs_from_title(title: Optional[str]) -> dict[str, str]:
    """Extract common notebook attributes embedded in ML titles."""
    specs: dict[str, str] = {}
    if not title:
        return specs
    t = title

    m = re.search(
        r"(intel\s+core\s*(?:ultra\s*)?(?:i?\d[^,;/]*)|"
        r"amd\s+ryzen\s*(?:ai\s*)?\d[^,;/]*|"
        r"snapdragon[^,;/]*|"
        r"apple\s+m\d[^,;/]*)",
        t,
        re.I,
    )
    if m:
        specs["Processador"] = m.group(1).strip()

    m = re.search(
        r"(rtx\s*\d{3,4}\s*(?:ti|super)?|"
        r"gtx\s*\d{3,4}|"
        r"radeon\s*rx[^,;/]*|"
        r"intel\s+iris\s*xe|"
        r"geforce[^,;/]*)",
        t,
        re.I,
    )
    if m:
        specs["Placa de vídeo"] = m.group(1).strip()

    m = re.search(r"(\d+)\s*GB\s*(?:RAM|mem[oó]ria)?", t, re.I)
    if m:
        # Prefer explicit RAM near 'RAM' or before SSD
        m2 = re.search(r"(\d+)\s*GB\s*RAM", t, re.I)
        specs["Memória RAM"] = f"{(m2 or m).group(1)} GB"

    m = re.search(r"(\d+)\s*GB\s*SSD|SSD\s*(\d+)\s*GB|(\d+)\s*TB\s*SSD", t, re.I)
    if m:
        gb = m.group(1) or m.group(2)
        tb = m.group(3)
        specs["Armazenamento"] = f"{tb} TB SSD" if tb else f"{gb} GB SSD"

    return specs


def parse_json_ld_products(raw_scripts: list[str]) -> dict[str, Any]:
    for raw in raw_scripts:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            typ = item.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "Product" in types:
                return item
    return {}


async def extract_json_ld(page) -> dict[str, Any]:
    handles = page.locator('script[type="application/ld+json"]')
    scripts: list[str] = []
    try:
        count = await handles.count()
    except Exception:  # noqa: BLE001
        return {}
    for i in range(count):
        try:
            raw = await handles.nth(i).inner_text()
            if raw:
                scripts.append(raw)
        except Exception:  # noqa: BLE001
            continue
    return parse_json_ld_products(scripts)


async def extract_specs(page) -> dict[str, str]:
    specs: dict[str, str] = {}
    for selector in SPEC_ROW_SELECTORS:
        rows = page.locator(selector)
        try:
            count = await rows.count()
        except Exception:  # noqa: BLE001
            continue
        for i in range(min(count, 80)):
            row = rows.nth(i)
            try:
                cells = row.locator("th, td, dt, dd")
                cell_count = await cells.count()
                if cell_count >= 2:
                    key = (await cells.nth(0).inner_text()).strip()
                    value = (await cells.nth(1).inner_text()).strip()
                    if key and value:
                        specs[key] = value
            except Exception:  # noqa: BLE001
                continue
        if specs:
            break
    return specs


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
    from collector.evidence import listing_only_evidence, map_block_reason
    from collector.retailers.mercadolibre.classification import (
        classify_mercadolibre_product,
    )

    specs = specs_from_title(title)
    processor = pick_spec(specs, SPEC_ALIASES["processor"])
    gpu = pick_spec(specs, SPEC_ALIASES["gpu"])
    ram = pick_spec(specs, SPEC_ALIASES["ram"])
    storage = pick_spec(specs, SPEC_ALIASES["storage"])

    discovery_name = None
    if isinstance(extra_raw, dict):
        discovery_name = extra_raw.get("discovery_name")
    classified = classify_mercadolibre_product(
        title=title,
        category_raw=category_raw,
        specs=specs,
        discovery_name=str(discovery_name) if discovery_name else None,
    )
    evidence = listing_only_evidence(reason=map_block_reason(detail_page_status))

    raw = {
        "detail_page_status": detail_page_status,
        "source": "listing_card",
        "evidence": evidence.to_dict(),
        "classification": classified.to_dict(),
        "title_raw_language": "pt-BR",
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
        price_text=price_text,
        list_price_text=list_price_text,
        availability_text=None,
        promo_text=promo_text,
        processor=processor,
        gpu=gpu,
        ram=ram,
        storage=storage,
        specs=specs,
        raw_payload=raw,
    )
    # Prefer two-stage classifier over discovery-slug-contaminated detect_product_type.
    product.product_type = classified.product_type
    product.raw_payload["classification"] = classified.to_dict()
    product.raw_payload["evidence"] = evidence.to_dict()
    return product



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
) -> Any:
    html = await page.content()
    title_txt = await page.title()
    url = page.url
    if is_account_verification(html, url) or is_account_verification(title_txt, url):
        raise RuntimeError("mercadolibre_account_verification")
    if is_bot_challenge(html) or is_bot_challenge(title_txt):
        raise RuntimeError("mercadolibre_bot_challenge")

    title = await first_text(page.locator, PRODUCT_TITLE_SELECTORS) or fallback_title
    price_text = await first_text(page.locator, PRODUCT_PRICE_SELECTORS) or fallback_price
    # Attach cents when fraction-only
    try:
        cents = page.locator(".andes-money-amount__cents")
        if price_text and "," not in price_text and await cents.count():
            c = (await cents.first.inner_text()).strip()
            if c:
                price_text = f"{price_text},{c}"
    except Exception:  # noqa: BLE001
        pass

    list_price_text = (
        await first_text(page.locator, PRODUCT_WAS_PRICE_SELECTORS) or fallback_list_price
    )
    availability_text = await first_text(page.locator, PRODUCT_AVAILABILITY_SELECTORS)
    promo_text = fallback_promo
    try:
        disc = page.locator(".andes-money-amount__discount, .ui-pdp-price__discount")
        if await disc.count():
            promo_text = (await disc.first.inner_text()).strip() or promo_text
    except Exception:  # noqa: BLE001
        pass

    json_ld = await extract_json_ld(page)
    if json_ld:
        title = title or json_ld.get("name")
        offers = json_ld.get("offers") or {}
        if isinstance(offers, list) and offers:
            offers = offers[0]
        if isinstance(offers, dict):
            if offers.get("price") is not None and not price_text:
                price_text = str(offers.get("price"))
            availability_text = availability_text or str(offers.get("availability") or "")

    specs_from_dom = await extract_specs(page)
    specs_table_present = bool(specs_from_dom)
    specs = dict(specs_from_dom)
    for key, value in specs_from_title(title).items():
        specs.setdefault(key, value)

    processor = pick_spec(specs, SPEC_ALIASES["processor"])
    gpu = pick_spec(specs, SPEC_ALIASES["gpu"])
    ram = pick_spec(specs, SPEC_ALIASES["ram"])
    storage = pick_spec(specs, SPEC_ALIASES["storage"])

    sku = extract_mlb_id(url, title or "") or fallback_sku

    from collector.evidence import product_page_evidence
    from collector.retailers.mercadolibre.classification import (
        classify_mercadolibre_product,
    )

    classified = classify_mercadolibre_product(
        title=title,
        category_raw=category_raw,
        specs=specs,
    )
    evidence = product_page_evidence(specs_available=specs_table_present)

    product = build_normalized_product(
        retailer_code=retailer_code,
        country_code=country_code,
        currency=currency,
        retailer_sku=sku,
        source_url=url.split("?")[0].split("#")[0],
        title=title,
        category_raw=category_raw,
        price_text=price_text,
        list_price_text=list_price_text,
        availability_text=availability_text,
        promo_text=promo_text,
        processor=processor,
        gpu=gpu,
        ram=ram,
        storage=storage,
        specs=specs,
        raw_payload={
            "detail_page_status": "ok",
            "source": "product_page",
            "json_ld": bool(json_ld),
            "evidence": evidence.to_dict(),
            "classification": classified.to_dict(),
            "title_raw_language": "pt-BR",
            "specs_table_present": specs_table_present,
        },
    )
    product.product_type = classified.product_type
    return product

