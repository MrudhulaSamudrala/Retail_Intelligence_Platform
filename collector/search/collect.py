"""Playwright search-result collection with explicit pagination."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus, urlencode, urljoin, urlparse, parse_qs, urlunparse

from playwright.async_api import Page

from collector.browser import BrowserSession
from collector.classification import classify_brand, detect_oem
from collector.retailers.newegg.listing import (
    extract_item_number,
    extract_listings_from_page,
    is_product_href,
)
from collector.retailers.newegg.product_page import is_bot_challenge
from collector.search.config import KeywordTarget, SovConfig, load_sov_config
from collector.search.models import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_ZERO,
    SearchHit,
    SearchRunResult,
)

logger = logging.getLogger("collector.search.collect")


def build_newegg_search_url(keyword: str, *, page: int = 1) -> str:
    params = {"d": keyword}
    if page > 1:
        params["page"] = str(page)
    return f"https://www.newegg.com/p/pl?{urlencode(params)}"


def build_mercadolibre_search_url(keyword: str, *, page: int = 1) -> str:
    """Mercado Libre BR list URL. Page 1 has no offset; later pages use _Desde_."""
    slug = re.sub(r"\s+", "-", keyword.strip().lower())
    slug = re.sub(r"[^a-z0-9\-áéíóúãõâêôç]", "", slug)
    base = f"https://lista.mercadolivre.com.br/{slug}"
    if page <= 1:
        return base
    # ML uses 1-based item offsets of ~48/50 depending on layout; use 48.
    offset = 1 + (page - 1) * 48
    return f"{base}_Desde_{offset}_NoIndex_True"


def build_search_url(retailer_code: str, keyword: str, *, page: int = 1) -> str:
    if retailer_code == "newegg":
        return build_newegg_search_url(keyword, page=page)
    if retailer_code == "mercadolibre":
        return build_mercadolibre_search_url(keyword, page=page)
    raise ValueError(f"Unsupported retailer for search: {retailer_code}")


async def _has_next_page_newegg(page: Page, current_page: int) -> bool:
    # Explicit next control or higher page link
    next_loc = page.locator(
        "button[title*='Next' i], a[title*='Next' i], "
        ".list-tool-pagination button.btn:has-text('>'), "
        f"a[href*='page={current_page + 1}']"
    )
    try:
        if await next_loc.count() > 0:
            # Disabled next means no further pages
            first = next_loc.first
            disabled = await first.get_attribute("disabled")
            aria = await first.get_attribute("aria-disabled")
            classes = (await first.get_attribute("class")) or ""
            if disabled is not None or aria == "true" or "disabled" in classes.lower():
                return False
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


async def _has_next_page_mercadolibre(page: Page) -> bool:
    next_loc = page.locator(
        "li.andes-pagination__button--next a, "
        "a[title*='Seguinte' i], a[title*='Siguiente' i], a.andes-pagination__link[title*='Next' i]"
    )
    try:
        return await next_loc.count() > 0
    except Exception:  # noqa: BLE001
        return False


async def _extract_mercadolibre_listings(page: Page) -> list[dict]:
    """Extract ML search/ofertas cards via modern poly-card + legacy ui-search DOM."""
    js = """
    () => {
      const cardSels = [
        'div.poly-card',
        'li.ui-search-layout__item',
        'div.ui-search-result',
        '.ui-search-result__wrapper'
      ];
      const titleSels = [
        'a.poly-component__title',
        'h2.ui-search-item__title',
        'a.ui-search-link',
        'a.ui-search-item__group__element',
        'a.ui-search-link-title'
      ];
      const seenEl = new Set();
      const cards = [];
      for (const sel of cardSels) {
        for (const el of Array.from(document.querySelectorAll(sel))) {
          if (seenEl.has(el)) continue;
          seenEl.add(el);
          cards.push(el);
        }
      }
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
      const out = [];
      const seen = new Set();
      for (const card of cards) {
        let a = null;
        for (const sel of titleSels) {
          a = card.querySelector(sel);
          if (a && a.href) break;
        }
        if (!a) a = card.querySelector('a[href*=\"/p/\"], a[href*=\"MLB\"]');
        if (!a || !a.href) continue;
        const href = a.href;
        if (!href || seen.has(href)) continue;
        if (/account-verification|click1\\.mercadolivre/i.test(href)) continue;
        seen.add(href);
        const title = (a.textContent || '').replace(/\\s+/g, ' ').trim();
        if (!title) continue;
        let sku = null;
        const m = href.match(/\\/p\\/(MLB\\d+)/i) || href.match(/wid=(MLB\\d+)/i) || href.match(/MLB-?(\\d+)/i);
        if (m) {
          const raw = (m[1] || m[0] || '').toUpperCase().replace('MLB-', 'MLB');
          sku = raw.startsWith('MLB') ? raw.replace(/^MLB(?=\\d)/, 'MLB') : ('MLB' + raw.replace(/\\D/g, ''));
          if (!/^MLB\\d+$/i.test(sku)) {
            const m2 = href.match(/MLB-?(\\d+)/i);
            if (m2) sku = 'MLB' + m2[1];
          }
        }
        const sponsored = !!(card.querySelector('[class*=\"advertising\"], [class*=\"sponsored\"], .ui-search-item__ad-label, .poly-component__ads-promotions'));
        out.push({
          title,
          href,
          sku,
          sponsored,
          selector: (card.className || 'poly-card').toString().slice(0, 80),
        });
      }
      return out;
    }
    """
    return await page.evaluate(js)


def _is_ml_verification_page(url: str, title: str, html: str) -> bool:
    blob = f"{url} {title} {html}".lower()
    return (
        "account-verification" in blob
        or "para continuar, acesse" in blob
        or "olá! para continuar" in blob
        or "ola! para continuar" in blob
    )


def build_mercadolibre_fallback_url(keyword: str) -> str:
    """Ofertas query fallback when lista.* is verification-gated."""
    q = quote_plus(keyword.strip())
    return f"https://www.mercadolivre.com.br/ofertas?q={q}"



def _classify_hit_brand_oem(title: str, features: list[str] | None = None) -> tuple[str, Optional[str]]:
    feature_blob = " ".join(features or [])
    brand, _reason = classify_brand(title=title, description=feature_blob or None)
    oem_raw = detect_oem(title, feature_blob or None)
    oem = None if oem_raw == "UNKNOWN" else oem_raw
    return brand, oem


async def run_keyword_search(
    session: BrowserSession,
    target: KeywordTarget,
    *,
    config: SovConfig | None = None,
) -> SearchRunResult:
    """Collect paginated search results for one keyword target."""
    cfg = config or load_sov_config()
    observed_at = datetime.now(timezone.utc)
    result = SearchRunResult(
        retailer_code=target.retailer_code,
        country_code=target.country_code,
        keyword=target.keyword,
        collection_status=STATUS_FAILED,
        pages_collected=0,
        observed_at=observed_at,
    )

    page = await session.new_page()
    hits: list[SearchHit] = []
    pages_ok = 0
    pagination_reliable = True
    stopped_at_cap = False
    first_url = build_search_url(target.retailer_code, target.keyword, page=1)
    result.search_url = first_url

    try:
        for page_number in range(1, cfg.max_pages + 1):
            if len(hits) >= cfg.max_results_per_keyword:
                stopped_at_cap = True
                break
            url = build_search_url(
                target.retailer_code, target.keyword, page=page_number
            )
            await session.goto(page, url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:  # noqa: BLE001
                pass

            title = await page.title()
            html = await page.content()
            if is_bot_challenge(title) or is_bot_challenge(html):
                result.error = f"bot_challenge title={title!r}"
                pagination_reliable = False
                break

            page_hits: list[SearchHit] = []
            if target.retailer_code == "newegg":
                listings = await extract_listings_from_page(page)
                for idx, cand in enumerate(listings):
                    global_pos = len(hits) + idx + 1
                    if global_pos > cfg.max_results_per_keyword:
                        break
                    features = list((cand.raw or {}).get("features") or [])
                    brand, oem = _classify_hit_brand_oem(cand.title or "", features)
                    page_hits.append(
                        SearchHit(
                            keyword=target.keyword,
                            retailer_code=target.retailer_code,
                            country_code=target.country_code,
                            position=global_pos,
                            page_number=page_number,
                            retailer_sku=cand.retailer_sku,
                            source_url=cand.source_url,
                            title=cand.title,
                            brand=brand,
                            oem=oem,
                            is_sponsored=False,
                            evidence_text=cand.title,
                            selector=".item-cell",
                            search_url=url,
                            details={"features": features[:8]} if features else {},
                        )
                    )
                has_next = await _has_next_page_newegg(page, page_number)
            else:
                # Mercado Libre: lista.* often redirects to account-verification in
                # automated CDP sessions. Detect and retry via ofertas?q= fallback.
                used_fallback = False
                if _is_ml_verification_page(page.url, title, html):
                    fallback = build_mercadolibre_fallback_url(target.keyword)
                    logger.warning(
                        "ml_search_verification_fallback",
                        extra={
                            "event": "ml_search_verification_fallback",
                            "url": page.url,
                            "fallback": fallback,
                        },
                    )
                    await session.goto(page, fallback, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2500)
                    title = await page.title()
                    html = await page.content()
                    url = fallback
                    used_fallback = True
                    pagination_reliable = False
                    result.details["ml_search_fallback"] = "ofertas_query"
                    if page_number == 1:
                        result.search_url = fallback

                if _is_ml_verification_page(page.url, title, html):
                    result.error = "mercadolibre_account_verification"
                    pagination_reliable = False
                    if page_number == 1 and not hits:
                        result.collection_status = STATUS_FAILED
                        result.pages_collected = 1
                        result.hits = []
                        return result
                    break

                for _ in range(2):
                    await page.mouse.wheel(0, 1800)
                    await page.wait_for_timeout(600)

                raw_cards = await _extract_mercadolibre_listings(page)
                for idx, card in enumerate(raw_cards):
                    global_pos = len(hits) + idx + 1
                    if global_pos > cfg.max_results_per_keyword:
                        break
                    title_text = card.get("title") or ""
                    brand, oem = _classify_hit_brand_oem(title_text)
                    sku = card.get("sku")
                    href = card.get("href")
                    page_hits.append(
                        SearchHit(
                            keyword=target.keyword,
                            retailer_code=target.retailer_code,
                            country_code=target.country_code,
                            position=global_pos,
                            page_number=page_number,
                            retailer_sku=sku,
                            source_url=href,
                            title=title_text,
                            brand=brand,
                            oem=oem,
                            is_sponsored=bool(card.get("sponsored")),
                            evidence_text=title_text,
                            selector=card.get("selector") or "poly-card",
                            search_url=url,
                            details={"fallback": used_fallback} if used_fallback else {},
                        )
                    )
                has_next = (
                    False
                    if used_fallback
                    else await _has_next_page_mercadolibre(page)
                )

            if not page_hits:
                if page_number == 1:
                    result.collection_status = STATUS_ZERO
                    result.pages_collected = 1
                    result.pagination_reliable = True
                    result.hits = []
                    return result
                # Empty later page → stop; prior pages kept as partial/complete
                if cfg.stop_on_empty_page:
                    break

            hits.extend(page_hits)
            pages_ok += 1

            if not has_next:
                break
            # If we couldn't detect next but max_pages allows more, mark unreliable
            # only when we stop early due to missing next control after empty detection
        else:
            # Exhausted max_pages while next may still exist
            if pages_ok >= cfg.max_pages:
                # Check one more time if next exists on last page
                pagination_reliable = False
                result.details["stopped_reason"] = "max_pages_reached"

        result.hits = hits
        result.pages_collected = pages_ok
        if stopped_at_cap:
            pagination_reliable = False
            result.details["stopped_reason"] = "max_results_per_keyword"
        result.pagination_reliable = pagination_reliable
        if pages_ok == 0:
            result.collection_status = STATUS_FAILED
            result.error = result.error or "no_pages_collected"
        elif not hits:
            result.collection_status = STATUS_ZERO
        elif not pagination_reliable:
            result.collection_status = STATUS_PARTIAL
        else:
            result.collection_status = STATUS_COMPLETE

        logger.info(
            "search_collected",
            extra={
                "event": "search_collected",
                "retailer": target.retailer_code,
                "country": target.country_code,
                "count": len(hits),
                "url": first_url,
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)
        result.hits = hits
        result.pages_collected = pages_ok
        result.collection_status = STATUS_PARTIAL if hits else STATUS_FAILED
        result.pagination_reliable = False
        logger.warning(
            "search_failed",
            extra={
                "event": "search_failed",
                "retailer": target.retailer_code,
                "error": str(exc),
                "url": first_url,
            },
        )
        return result
    finally:
        await page.close()


async def collect_search_visibility(
    *,
    retailer_codes: list[str] | None = None,
    country_codes: list[str] | None = None,
    keywords: list[str] | None = None,
    limit_per_retailer: int | None = None,
    browser: BrowserSession | None = None,
) -> list[SearchRunResult]:
    """Run configured retailer×country×keyword searches (no product catalog writes)."""
    from collector.search.config import load_keyword_targets

    cfg = load_sov_config()
    targets = load_keyword_targets(
        retailer_codes=retailer_codes,
        country_codes=country_codes,
        keywords=keywords,
        limit_per_retailer=limit_per_retailer,
    )
    owns = browser is None
    session = browser or BrowserSession()
    results: list[SearchRunResult] = []
    try:
        if owns:
            await session.__aenter__()
        for target in targets:
            results.append(await run_keyword_search(session, target, config=cfg))
    finally:
        if owns:
            await session.__aexit__(None, None, None)
    return results
