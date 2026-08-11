"""Newegg US retailer collector adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from collector.base import ListingCandidate, RetailerCollector
from collector.browser import BrowserSession
from collector.config_loader import get_retailer
from collector.normalize import NormalizedProduct
from collector.retailers.newegg.discovery import load_discovery_config
from collector.retailers.newegg.listing import dedupe_candidates, extract_listings_from_page
from collector.retailers.newegg.product_page import is_bot_challenge, parse_product_page

logger = logging.getLogger("collector.newegg")


class NeweggCollector(RetailerCollector):
    code = "newegg"

    def __init__(self) -> None:
        cfg = get_retailer("newegg")
        self.country_code = cfg["country_code"]
        self.currency = cfg["currency"]
        self.base_url = cfg["base_url"]
        self.discovery = load_discovery_config()

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
                    "newegg_discovery_open",
                    extra={"event": "newegg_discovery_open", "url": url, "retailer": self.code},
                )
                await session.goto(page, url)
                await page.wait_for_timeout(2500)
                content = await page.content()
                title = await page.title()
                if is_bot_challenge(content) or is_bot_challenge(title):
                    path = await session.screenshot(page, label="newegg_bot_challenge_listing")
                    raise RuntimeError(
                        f"Newegg blocked listing discovery (bot challenge). screenshot={path}"
                    )

                # Mild scroll to trigger lazy content.
                await page.mouse.wheel(0, 2400)
                await page.wait_for_timeout(1000)

                batch = await extract_listings_from_page(page, category_raw=category_raw)
                logger.info(
                    "newegg_listing_batch",
                    extra={
                        "event": "newegg_listing_batch",
                        "url": url,
                        "count": len(batch),
                        "retailer": self.code,
                    },
                )
                collected.extend(batch)
                if len(dedupe_candidates(collected)) >= limit:
                    break
                await asyncio.sleep(1.5)
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
            await session.goto(page, candidate.source_url)
            await page.wait_for_timeout(2000)
            content = await page.content()
            if is_bot_challenge(content) or is_bot_challenge(await page.title()):
                path = await session.screenshot(page, label=f"newegg_bot_{candidate.retailer_sku}")
                raise RuntimeError(
                    f"Newegg bot challenge on product page. screenshot={path}"
                )

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
            # Keep listing promo if product page omitted it.
            if not product.promo_text and candidate.promo_text:
                product.promo_text = candidate.promo_text
                product.is_on_promotion = True
                product.promo_type = product.promo_type or "sale"
            await session.screenshot(page, label=f"newegg_product_{product.retailer_sku}")
            await asyncio.sleep(0.8)
            return product
        finally:
            await page.close()


def build_collector() -> NeweggCollector:
    return NeweggCollector()
