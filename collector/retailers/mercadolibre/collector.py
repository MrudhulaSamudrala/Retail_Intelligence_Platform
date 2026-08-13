"""Mercado Libre Brazil retailer collector adapter."""

from __future__ import annotations

import asyncio
import logging

from collector.base import ListingCandidate, RetailerCollector
from collector.browser import BrowserSession
from collector.config_loader import get_retailer
from collector.normalize import NormalizedProduct
from collector.retailers.mercadolibre.discovery import load_discovery_config
from collector.retailers.mercadolibre.listing import dedupe_candidates, extract_listings_from_page
from collector.retailers.mercadolibre.product_page import (
    build_from_listing,
    is_account_verification,
    is_bot_challenge,
    parse_product_page,
)

logger = logging.getLogger("collector.mercadolibre")


class MercadoLibreCollector(RetailerCollector):
    code = "mercadolibre"

    def __init__(self) -> None:
        cfg = get_retailer("mercadolibre")
        self.country_code = cfg["country_code"]
        self.currency = cfg["currency"]
        self.base_url = cfg["base_url"]
        self.discovery = load_discovery_config()
        self.allow_listing_only = bool(
            self.discovery.get("allow_listing_only_fallback", True)
        )

    async def _page_blocked(self, page) -> str | None:
        content = await page.content()
        title = await page.title()
        url = page.url
        if is_account_verification(content, url) or is_account_verification(title, url):
            return "account_verification"
        if is_bot_challenge(content) or is_bot_challenge(title):
            return "bot_challenge"
        return None

    async def discover_listings(
        self,
        session: BrowserSession,
        *,
        limit: int,
    ) -> list[ListingCandidate]:
        page = await session.new_page()
        collected: list[ListingCandidate] = []
        try:
            for entry in self.discovery.get("discovery", []):
                url = entry["url"]
                category_raw = entry.get("name")
                logger.info(
                    "mercadolibre_discovery_open",
                    extra={
                        "event": "mercadolibre_discovery_open",
                        "url": url,
                        "retailer": self.code,
                    },
                )
                await session.goto(page, url, wait_until="domcontentloaded")
                await page.wait_for_timeout(2500)
                blocked = await self._page_blocked(page)
                if blocked:
                    logger.warning(
                        "mercadolibre_discovery_blocked",
                        extra={
                            "event": "mercadolibre_discovery_blocked",
                            "reason": blocked,
                            "url": page.url,
                            "retailer": self.code,
                        },
                    )
                    await session.screenshot(page, label=f"ml_discovery_{blocked}")
                    continue

                for _ in range(3):
                    await page.mouse.wheel(0, 2200)
                    await page.wait_for_timeout(800)

                batch = await extract_listings_from_page(page, category_raw=category_raw)
                logger.info(
                    "mercadolibre_listing_batch",
                    extra={
                        "event": "mercadolibre_listing_batch",
                        "url": url,
                        "count": len(batch),
                        "retailer": self.code,
                    },
                )
                await session.screenshot(page, label="ml_listing_discovery")
                collected.extend(batch)
                if len(dedupe_candidates(collected)) >= limit:
                    break
                await asyncio.sleep(1.0)
        finally:
            await page.close()

        unique = dedupe_candidates(collected)
        return unique[: max(limit, 1)]

    async def fetch_product(
        self,
        session: BrowserSession,
        candidate: ListingCandidate,
    ) -> NormalizedProduct:
        page = await session.new_page()
        try:
            await session.goto(
                page, candidate.source_url, wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(2000)
            blocked = await self._page_blocked(page)
            if blocked:
                logger.warning(
                    "mercadolibre_pdp_blocked",
                    extra={
                        "event": "mercadolibre_pdp_blocked",
                        "reason": blocked,
                        "sku": candidate.retailer_sku,
                        "url": candidate.source_url,
                        "retailer": self.code,
                    },
                )
                await session.screenshot(
                    page, label=f"ml_pdp_{blocked}_{candidate.retailer_sku}"
                )
                if not self.allow_listing_only:
                    raise RuntimeError(f"Mercado Libre PDP blocked: {blocked}")
                return build_from_listing(
                    retailer_code=self.code,
                    country_code=self.country_code,
                    currency=self.currency,
                    sku=candidate.retailer_sku,
                    source_url=candidate.source_url,
                    title=candidate.title,
                    price_text=candidate.price_text,
                    list_price_text=candidate.list_price_text,
                    promo_text=candidate.promo_text,
                    category_raw=candidate.category_raw,
                    detail_page_status=blocked,
                    extra_raw=dict(candidate.raw or {}),
                )

            try:
                product = await parse_product_page(
                    page,
                    retailer_code=self.code,
                    country_code=self.country_code,
                    currency=self.currency,
                    fallback_sku=candidate.retailer_sku,
                    fallback_title=candidate.title,
                    fallback_price=candidate.price_text,
                    fallback_list_price=candidate.list_price_text,
                    fallback_promo=candidate.promo_text,
                    category_raw=candidate.category_raw,
                )
            except RuntimeError as exc:
                if self.allow_listing_only and "verification" in str(exc).lower():
                    return build_from_listing(
                        retailer_code=self.code,
                        country_code=self.country_code,
                        currency=self.currency,
                        sku=candidate.retailer_sku,
                        source_url=candidate.source_url,
                        title=candidate.title,
                        price_text=candidate.price_text,
                        list_price_text=candidate.list_price_text,
                        promo_text=candidate.promo_text,
                        category_raw=candidate.category_raw,
                        detail_page_status="account_verification",
                        extra_raw=dict(candidate.raw or {}),
                    )
                raise

            if not product.promo_text and candidate.promo_text:
                product.promo_text = candidate.promo_text
                product.is_on_promotion = True
                product.promo_type = product.promo_type or "sale"
            await session.screenshot(
                page, label=f"ml_product_{product.retailer_sku}"
            )
            await asyncio.sleep(0.5)
            return product
        finally:
            await page.close()


def build_collector() -> MercadoLibreCollector:
    return MercadoLibreCollector()
