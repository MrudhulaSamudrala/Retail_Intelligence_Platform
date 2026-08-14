"""Newegg listing-page parsing helpers (pure functions where possible)."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse

from collector.base import ListingCandidate
from collector.retailers.newegg.selectors import (
    LISTING_ITEM_SELECTORS,
    LISTING_PRICE_SELECTORS,
    LISTING_PROMO_SELECTORS,
    LISTING_TITLE_SELECTORS,
    LISTING_WAS_PRICE_SELECTORS,
)

ITEM_NUMBER_RE = re.compile(r"(N\d{2}E\d{9,}[A-Z0-9]*)", re.IGNORECASE)
ITEM_PATH_RE = re.compile(r"/p/([A-Za-z0-9\-]+)", re.IGNORECASE)
# Newegg search uses /p/pl?... — never treat that path token as a product SKU.
INVALID_PATH_SKUS = {"PL", "P", "PAGE", "PAGES"}


def extract_item_number(url: str, text: str = "") -> Optional[str]:
    for source in (url, text):
        match = ITEM_NUMBER_RE.search(source or "")
        if match:
            return match.group(1).upper()
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("Item", "ItemNumber", "item"):
        if key in qs and qs[key]:
            value = qs[key][0].strip().upper()
            if value and value not in INVALID_PATH_SKUS:
                return value
    path_match = ITEM_PATH_RE.search(parsed.path or "")
    if path_match:
        token = path_match.group(1).upper()
        # Real Newegg product path IDs are longer than search stubs like "PL".
        if token in INVALID_PATH_SKUS or len(token) < 6:
            return None
        return token
    return None


def absolute_newegg_url(href: str, base: str = "https://www.newegg.com") -> str:
    return urljoin(base, href)


def is_product_href(href: str) -> bool:
    """Return True for product detail links, False for search/category stubs."""
    if not href:
        return False
    parsed = urlparse(absolute_newegg_url(href))
    path = (parsed.path or "").lower()
    if path.rstrip("/") in {"/p/pl", "/p"}:
        return False
    if "/p/pl" == path or path.startswith("/p/pl/"):
        return False
    return "/p/" in path


def parse_listing_card_html(
    *,
    title: Optional[str],
    href: Optional[str],
    price_text: Optional[str],
    list_price_text: Optional[str],
    promo_text: Optional[str],
    category_raw: Optional[str] = None,
) -> Optional[ListingCandidate]:
    if not href or not title:
        return None
    if not is_product_href(href):
        return None
    url = absolute_newegg_url(href)
    sku = extract_item_number(url, title)
    if not sku:
        return None
    return ListingCandidate(
        retailer_sku=sku,
        source_url=url.split("?")[0],
        title=title.strip(),
        price_text=(price_text or "").strip() or None,
        list_price_text=(list_price_text or "").strip() or None,
        promo_text=(promo_text or "").strip() or None,
        category_raw=category_raw,
        raw={"listing_href": href},
    )


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


async def first_attr(locator_factory, selectors: list[str], attr: str) -> Optional[str]:
    for selector in selectors:
        loc = locator_factory(selector)
        try:
            if await loc.count() == 0:
                continue
            value = await loc.first.get_attribute(attr)
            if value:
                return value
        except Exception:  # noqa: BLE001
            continue
    return None


async def extract_listings_from_page(page, *, category_raw: Optional[str] = None) -> list[ListingCandidate]:
    """Extract listing candidates using fallback selectors."""
    items = []
    for item_sel in LISTING_ITEM_SELECTORS:
        cards = page.locator(item_sel)
        count = await cards.count()
        if count == 0:
            continue
        for i in range(count):
            card = cards.nth(i)

            # Must be sync: first_text/first_attr call this directly (not awaited).
            def factory(sel: str, _card=card):
                return _card.locator(sel)

            title = await first_text(factory, LISTING_TITLE_SELECTORS)
            href = await first_attr(factory, LISTING_TITLE_SELECTORS, "href")
            if not href:
                # Sometimes title text and link are separate.
                href = await first_attr(factory, ["a[href*='/p/']"], "href")
            price_text = await first_text(factory, LISTING_PRICE_SELECTORS)
            list_price_text = await first_text(factory, LISTING_WAS_PRICE_SELECTORS)
            promo_text = await first_text(factory, LISTING_PROMO_SELECTORS)
            candidate = parse_listing_card_html(
                title=title,
                href=href,
                price_text=price_text,
                list_price_text=list_price_text,
                promo_text=promo_text,
                category_raw=category_raw,
            )
            if candidate:
                # Capture feature bullets when present (GPU / CPU / RAM / SSD lines).
                try:
                    bullets = card.locator("ul.item-features li, .item-features li")
                    bullet_count = min(await bullets.count(), 12)
                    features = []
                    for bi in range(bullet_count):
                        text = (await bullets.nth(bi).inner_text()).strip()
                        if text and len(text) < 200:
                            features.append(text)
                    if features:
                        candidate.raw["features"] = features
                except Exception:  # noqa: BLE001
                    pass
                try:
                    cls = ((await card.get_attribute("class")) or "").lower()
                    tile = ((await card.inner_text()) or "").strip()
                    if tile:
                        candidate.raw["tile_text"] = tile[:2000]
                    head = tile[:240].lower()
                    candidate.raw["is_sponsored"] = (
                        "sponsored" in cls
                        or "sponsored" in head
                        or "ad-flag" in cls
                    )
                except Exception:  # noqa: BLE001
                    candidate.raw.setdefault("is_sponsored", False)
                items.append(candidate)
        if items:
            break

    # Fallback: harvest product anchors if card structure failed.
    if not items:
        anchors = page.locator("a[href*='/p/']")
        count = await anchors.count()
        seen = set()
        for i in range(min(count, 80)):
            a = anchors.nth(i)
            try:
                href = await a.get_attribute("href")
                title = (await a.inner_text()).strip()
            except Exception:  # noqa: BLE001
                continue
            if not href or href in seen:
                continue
            seen.add(href)
            candidate = parse_listing_card_html(
                title=title or None,
                href=href,
                price_text=None,
                list_price_text=None,
                promo_text=None,
                category_raw=category_raw,
            )
            if candidate and candidate.title and len(candidate.title) >= 8:
                items.append(candidate)
    return items


def dedupe_candidates(candidates: list[ListingCandidate]) -> list[ListingCandidate]:
    seen: set[str] = set()
    unique: list[ListingCandidate] = []
    for item in candidates:
        if item.retailer_sku in seen:
            continue
        seen.add(item.retailer_sku)
        unique.append(item)
    return unique
