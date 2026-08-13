"""Mercado Libre listing helpers (pure functions + Playwright extraction)."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse, urlunparse

from collector.base import ListingCandidate
from collector.retailers.mercadolibre.selectors import (
    LISTING_CARD_SELECTORS,
    LISTING_DISCOUNT_SELECTORS,
    LISTING_PRICE_CENTS_SELECTORS,
    LISTING_PRICE_SELECTORS,
    LISTING_TITLE_SELECTORS,
    LISTING_WAS_PRICE_SELECTORS,
)

# Catalog product: /p/MLB49089309
CATALOG_ID_RE = re.compile(r"/p/(MLB\d+)", re.IGNORECASE)
# Marketplace item: MLB-2794030545 or MLB2794030545
ITEM_ID_RE = re.compile(r"\b(MLB)-?(\d{6,})\b", re.IGNORECASE)
WID_RE = re.compile(r"[?&#]wid=(MLB\d+)", re.IGNORECASE)


def normalize_mlb_id(raw: str) -> str:
    """Normalize to MLB + digits (no hyphen)."""
    text = (raw or "").strip().upper().replace("MLB-", "MLB")
    m = re.match(r"(MLB)(\d+)$", text)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m2 = ITEM_ID_RE.search(text)
    if m2:
        return f"{m2.group(1).upper()}{m2.group(2)}"
    return text


def extract_mlb_id(url: str, text: str = "") -> Optional[str]:
    """Prefer catalog /p/MLB id, then wid=, then MLB- item id in URL/text."""
    for source in (url or "", text or ""):
        m = CATALOG_ID_RE.search(source)
        if m:
            return normalize_mlb_id(m.group(1))
    m = WID_RE.search(url or "")
    if m:
        return normalize_mlb_id(m.group(1))
    for source in (url or "", text or ""):
        m = ITEM_ID_RE.search(source)
        if m:
            return normalize_mlb_id(f"{m.group(1)}{m.group(2)}")
    return None


def canonical_product_url(url: str) -> str:
    """Strip tracking query/fragment while keeping stable path."""
    if not url:
        return url
    parsed = urlparse(url)
    # Keep path only for catalog pages; drop deal tracking params.
    clean = parsed._replace(query="", fragment="")
    return urlunparse(clean)


def is_product_href(href: str) -> bool:
    if not href:
        return False
    lower = href.lower()
    if "account-verification" in lower:
        return False
    if "click1.mercadolivre" in lower or "click1.mercadolibre" in lower:
        return False
    if "/p/mlb" in lower:
        return True
    if re.search(r"mlb-?\d{6,}", lower):
        return True
    return False


def combine_price_parts(fraction: Optional[str], cents: Optional[str]) -> Optional[str]:
    frac = (fraction or "").strip()
    if not frac:
        return None
    c = (cents or "").strip()
    if c:
        return f"{frac},{c}"
    return frac


def parse_listing_card(
    *,
    title: Optional[str],
    href: Optional[str],
    price_text: Optional[str],
    list_price_text: Optional[str] = None,
    promo_text: Optional[str] = None,
    category_raw: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[ListingCandidate]:
    if not href or not title:
        return None
    if not is_product_href(href):
        return None
    sku = extract_mlb_id(href, title)
    if not sku:
        return None
    return ListingCandidate(
        retailer_sku=sku,
        source_url=canonical_product_url(href),
        title=title.strip(),
        price_text=(price_text or "").strip() or None,
        list_price_text=(list_price_text or "").strip() or None,
        promo_text=(promo_text or "").strip() or None,
        category_raw=category_raw,
        raw={"listing_href": href, **(extra or {})},
    )


def dedupe_candidates(candidates: list[ListingCandidate]) -> list[ListingCandidate]:
    seen: set[str] = set()
    out: list[ListingCandidate] = []
    for cand in candidates:
        key = cand.retailer_sku.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


async def extract_listings_from_page(
    page,
    *,
    category_raw: Optional[str] = None,
) -> list[ListingCandidate]:
    """Extract poly-card / ui-search listing candidates from the current page."""
    js = """
    (args) => {
      const cardSels = args.cardSels || [];
      const titleSels = args.titleSels || [];
      const priceSels = args.priceSels || [];
      const centsSels = args.centsSels || [];
      const wasSels = args.wasSels || [];
      const discSels = args.discSels || [];
      const cards = [];
      const seenEl = new Set();
      for (const sel of cardSels) {
        for (const el of Array.from(document.querySelectorAll(sel))) {
          if (seenEl.has(el)) continue;
          seenEl.add(el);
          cards.push(el);
        }
      }
      // Fallback: title anchors themselves
      if (!cards.length) {
        for (const sel of titleSels) {
          for (const a of Array.from(document.querySelectorAll(sel))) {
            const root = a.closest('div.poly-card, li.ui-search-layout__item, div.ui-search-result') || a.parentElement;
            if (root && !seenEl.has(root)) {
              seenEl.add(root);
              cards.push(root);
            }
          }
        }
      }
      function firstText(root, sels) {
        for (const sel of sels) {
          const n = root.querySelector(sel);
          if (n && (n.textContent || '').trim()) return (n.textContent || '').trim();
        }
        return null;
      }
      const out = [];
      const seenHref = new Set();
      for (const card of cards) {
        let a = null;
        for (const sel of titleSels) {
          a = card.querySelector(sel);
          if (a && a.href) break;
        }
        if (!a) {
          a = card.querySelector('a[href*=\"/p/\"], a[href*=\"MLB\"]');
        }
        if (!a || !a.href) continue;
        const href = a.href;
        if (seenHref.has(href)) continue;
        seenHref.add(href);
        const title = (a.textContent || '').replace(/\\s+/g, ' ').trim();
        if (!title) continue;
        const fraction = firstText(card, priceSels);
        const cents = firstText(card, centsSels);
        const was = firstText(card, wasSels);
        const discount = firstText(card, discSels);
        out.push({
          title,
          href,
          price_fraction: fraction,
          price_cents: cents,
          list_price: was,
          discount,
        });
      }
      return out;
    }
    """
    raw_cards = await page.evaluate(
        js,
        {
            "cardSels": LISTING_CARD_SELECTORS,
            "titleSels": LISTING_TITLE_SELECTORS,
            "priceSels": LISTING_PRICE_SELECTORS,
            "centsSels": LISTING_PRICE_CENTS_SELECTORS,
            "wasSels": LISTING_WAS_PRICE_SELECTORS,
            "discSels": LISTING_DISCOUNT_SELECTORS,
        },
    )
    results: list[ListingCandidate] = []
    for card in raw_cards or []:
        price = combine_price_parts(card.get("price_fraction"), card.get("price_cents"))
        promo = card.get("discount")
        cand = parse_listing_card(
            title=card.get("title"),
            href=card.get("href"),
            price_text=price,
            list_price_text=card.get("list_price"),
            promo_text=promo,
            category_raw=category_raw,
            extra={"discount_text": promo},
        )
        if cand:
            results.append(cand)
    return results
