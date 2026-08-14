"""Newegg product-page parsing."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from collector.normalize import build_normalized_product
from collector.retailers.newegg.listing import extract_item_number, first_text
from collector.retailers.newegg.selectors import (
    BOT_CHALLENGE_MARKERS,
    PRODUCT_AVAILABILITY_SELECTORS,
    PRODUCT_PRICE_SELECTORS,
    PRODUCT_TITLE_SELECTORS,
    SPEC_ROW_SELECTORS,
)

SPEC_ALIASES = {
    "processor": [
        "cpu",
        "processor",
        "cpu type",
        "cpu name",
        "processor type",
    ],
    "gpu": [
        "gpu/vga type",
        "graphics card",
        "gpu type",
        "chipset",
        "gpu",
        "graphics",
        "video memory",
    ],
    "ram": [
        "memory",
        "ram",
        "system memory",
        "memory capacity",
        "memory size",
    ],
    "storage": [
        "storage",
        "ssd",
        "hdd",
        "hard drive",
        "storage capacity",
        "drive capacity",
    ],
}


def is_bot_challenge(html_or_text: str) -> bool:
    lowered = (html_or_text or "").lower()
    return any(marker in lowered for marker in BOT_CHALLENGE_MARKERS)


def pick_spec(specs: dict[str, str], aliases: list[str]) -> Optional[str]:
    lowered = {k.lower().strip(): v.strip() for k, v in specs.items() if v}

    def _usable(value: str, alias: str) -> bool:
        # Avoid treating VRAM-only values as the GPU model name.
        if alias in {"gpu", "graphics", "video memory", "gpu/vga type", "graphics card", "gpu type", "chipset"}:
            if re.fullmatch(r"\d+\s*GB(?:DDR\d)?", value, re.I):
                return False
        return bool(value)

    for alias in aliases:
        if alias in lowered and _usable(lowered[alias], alias):
            return lowered[alias]
    for key, value in lowered.items():
        for alias in aliases:
            if alias in key and _usable(value, alias):
                return value
    return None


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


async def extract_specs(page) -> dict[str, str]:
    specs: dict[str, str] = {}
    for selector in SPEC_ROW_SELECTORS:
        rows = page.locator(selector)
        try:
            count = await rows.count()
        except Exception:  # noqa: BLE001
            continue
        for i in range(count):
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


def specs_from_feature_bullets(features: list[str] | None) -> dict[str, str]:
    """Parse listing feature bullets like 'GPU: RTX 4060' into a specs dict."""
    specs: dict[str, str] = {}
    if not features:
        return specs
    for line in features:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            specs[key] = value
    return specs


async def extract_badge_signals(page) -> dict[str, Any]:
    """Collect observed Newegg DOM alt/title/badge text. Does not infer families."""
    try:
        payload = await page.evaluate(
            """() => {
              const take = (nodes, attr) => {
                const out = [];
                for (const el of nodes) {
                  const value = (attr ? el.getAttribute(attr) : (el.innerText || "")).trim();
                  if (value && value.length < 240 && !out.includes(value)) out.push(value);
                  if (out.length >= 40) break;
                }
                return out;
              };
              return {
                img_alts: take(document.querySelectorAll("img[alt]"), "alt"),
                img_titles: take(document.querySelectorAll("img[title]"), "title"),
                element_titles: take(
                  document.querySelectorAll("[class*='badge'][title], [class*='logo'][title]"),
                  "title"
                ),
                aria_labels: take(
                  document.querySelectorAll("[aria-label]"),
                  "aria-label"
                ),
                badge_texts: take(
                  document.querySelectorAll("[class*='badge'], [class*='brand-logo']"),
                  null
                ),
              };
            }"""
        )
    except Exception:  # noqa: BLE001
        return {"inspected": False}
    if not isinstance(payload, dict):
        return {"inspected": False}
    payload["inspected"] = True
    return payload


def listing_audit_from_card(
    *,
    title: Optional[str],
    source_url: Optional[str],
    listing_raw: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    raw = dict(listing_raw or {})
    badge_texts = list(raw.get("badge_texts") or [])
    return {
        "title": raw.get("title") or title,
        "tile_text": raw.get("tile_text"),
        "badge_texts": badge_texts,
        "selectors_used": ["listing_card"],
        "source_url": source_url,
        "available": True,
    }


def build_from_listing(
    *,
    retailer_code: str,
    country_code: str,
    currency: str,
    sku: str,
    source_url: str,
    title: Optional[str] = None,
    price_text: Optional[str] = None,
    list_price_text: Optional[str] = None,
    promo_text: Optional[str] = None,
    category_raw: Optional[str] = None,
    detail_page_status: str = "bot_challenge",
    listing_raw: Optional[dict[str, Any]] = None,
) -> Any:
    """Listing-only Newegg product when the PDP was blocked. No fake PDP fields."""
    listing_audit = listing_audit_from_card(
        title=title, source_url=source_url, listing_raw=listing_raw
    )
    return build_normalized_product(
        retailer_code=retailer_code,
        country_code=country_code,
        currency=currency,
        retailer_sku=sku,
        source_url=source_url,
        title=title,
        category_raw=category_raw,
        price_text=price_text,
        list_price_text=list_price_text,
        promo_text=promo_text,
        specs={},
        raw_payload={
            "source": "listing_card",
            "detail_page_status": detail_page_status,
            "listing_audit": listing_audit,
            "pdp_audit": {
                "badges_inspected": False,
                "media_inspected": False,
                "badge_texts": [],
                "brand_media_signals": [],
                "oem_media_signals": [],
                "selectors_used": [],
                "specs_available": False,
                "access_reason": "PDP_BLOCKED",
            },
            "badge_signals": {},
        },
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
    listing_features: Optional[list[str]] = None,
    listing_raw: Optional[dict[str, Any]] = None,
) -> Any:
    html = await page.content()
    if is_bot_challenge(html) or is_bot_challenge(await page.title()):
        raise RuntimeError("Newegg bot challenge page detected")

    title = await first_text(page.locator, PRODUCT_TITLE_SELECTORS) or fallback_title
    price_text = await first_text(page.locator, PRODUCT_PRICE_SELECTORS) or fallback_price
    availability_text = await first_text(page.locator, PRODUCT_AVAILABILITY_SELECTORS)
    promo_text = fallback_promo
    try:
        promo_loc = page.locator(".product-promo, .price-save, .item-promo")
        if await promo_loc.count():
            promo_text = (await promo_loc.first.inner_text()).strip() or promo_text
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

    specs = await extract_specs(page)
    # Merge listing bullets when product-page tables are sparse.
    for key, value in specs_from_feature_bullets(listing_features).items():
        specs.setdefault(key, value)

    processor = pick_spec(specs, SPEC_ALIASES["processor"])
    gpu = pick_spec(specs, SPEC_ALIASES["gpu"])
    ram = pick_spec(specs, SPEC_ALIASES["ram"])
    storage = pick_spec(specs, SPEC_ALIASES["storage"])

    # Title heuristics when specs missing.
    if not processor and title:
        m = re.search(
            r"(intel\s+core[^,;/]*|amd\s+ryzen[^,;/]*|snapdragon[^,;/]*|apple\s+m\d[^,;/]*)",
            title,
            re.I,
        )
        if m:
            processor = m.group(1).strip()
    if not gpu and title:
        m = re.search(r"(rtx\s*\d{3,4}[^,;/]*|radeon\s*rx[^,;/]*|geforce[^,;/]*)", title, re.I)
        if m:
            gpu = m.group(1).strip()

    sku = extract_item_number(page.url, title or "") or fallback_sku
    list_price_text = fallback_list_price
    try:
        was = page.locator(".price-was")
        if await was.count():
            list_price_text = (await was.first.inner_text()).strip() or list_price_text
    except Exception:  # noqa: BLE001
        pass

    badge_signals = await extract_badge_signals(page)
    inspected = badge_signals.get("inspected") is True
    listing_audit = listing_audit_from_card(
        title=fallback_title or title,
        source_url=page.url.split("?")[0],
        listing_raw=listing_raw,
    )
    pdp_audit = {
        "badges_inspected": inspected,
        "media_inspected": inspected,
        "badge_texts": list(badge_signals.get("badge_texts") or []),
        "brand_media_signals": list(badge_signals.get("img_alts") or [])
        + list(badge_signals.get("aria_labels") or []),
        "oem_media_signals": list(badge_signals.get("img_alts") or [])
        + list(badge_signals.get("img_titles") or []),
        "selectors_used": list(PRODUCT_TITLE_SELECTORS) + list(SPEC_ROW_SELECTORS),
        "specs_available": bool(specs),
    }
    return build_normalized_product(
        retailer_code=retailer_code,
        country_code=country_code,
        currency=currency,
        retailer_sku=sku,
        source_url=page.url.split("?")[0],
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
            "json_ld_present": bool(json_ld),
            "page_title": await page.title(),
            "listing_features": listing_features or [],
            "detail_page_status": "ok",
            "listing_audit": listing_audit,
            "pdp_audit": pdp_audit,
            "badge_signals": {
                "badge_texts": pdp_audit["badge_texts"],
                "img_alts": list(badge_signals.get("img_alts") or []),
                "img_titles": list(badge_signals.get("img_titles") or []),
                "element_titles": list(badge_signals.get("element_titles") or []),
                "aria_labels": list(badge_signals.get("aria_labels") or []),
            },
        },
    )
