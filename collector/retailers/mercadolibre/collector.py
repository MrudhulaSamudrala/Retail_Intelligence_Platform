"""Mercado Libre Brazil retailer collector adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from collector.base import ListingCandidate, RetailerCollector
from collector.browser import BrowserSession
from collector.config_loader import get_retailer
from collector.normalize import NormalizedProduct
from collector.retailers.mercadolibre.classification import (
    classify_mercadolibre_product,
    is_collection_eligible,
)
from collector.retailers.mercadolibre.discovery import load_discovery_config
from collector.retailers.mercadolibre.listing import dedupe_candidates, extract_listings_from_page
from collector.retailers.mercadolibre.product_page import (
    build_from_listing,
    is_account_verification,
    is_bot_challenge,
    parse_product_page,
)
from collector.retailers.mercadolibre.relevance import is_in_collection_scope, title_looks_irrelevant

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

    def is_in_collection_scope(self, product: NormalizedProduct) -> bool:
        raw = product.raw_payload or {}
        classification = raw.get("classification")
        if isinstance(classification, dict) and classification.get("status"):
            from collector.retailers.mercadolibre.classification import ClassificationResult

            result = ClassificationResult(
                status=str(classification.get("status")),
                product_type=str(classification.get("product_type") or product.product_type or "UNKNOWN"),
                confidence=float(classification.get("confidence") or 0.0),
                gaming=bool(classification.get("gaming")),
                hard_negative=bool(classification.get("hard_negative")),
                reasons=list(classification.get("reasons") or []),
            )
            return is_collection_eligible(result)
        return is_in_collection_scope(
            product_type=product.product_type,
            title=product.title,
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

    def _discovery_entries(self) -> list[tuple[str, dict[str, Any]]]:
        """Return (tier, entry) with primary first, then secondary/legacy."""
        entries: list[tuple[str, dict[str, Any]]] = []
        for item in self.discovery.get("discovery_primary") or []:
            entries.append(("primary", item))
        for item in self.discovery.get("discovery_secondary") or []:
            entries.append(("secondary", item))
        # Legacy single list (empty in new YAML).
        for item in self.discovery.get("discovery") or []:
            entries.append(("legacy", item))
        return entries

    async def _harvest_entry(
        self,
        session: BrowserSession,
        page,
        entry: dict[str, Any],
        *,
        tier: str,
        card_budget: int,
        collected: list[ListingCandidate],
    ) -> tuple[int, int]:
        """Harvest one discovery URL. Returns (kept, irrelevant)."""
        limits = self.discovery.get("limits") or {}
        url = entry["url"]
        discovery_name = entry.get("name")
        surface = entry.get("surface") or tier
        category_raw = entry.get("category_label") or None
        irrelevant_seen = 0
        kept = 0

        logger.info(
            "mercadolibre_discovery_open",
            extra={
                "event": "mercadolibre_discovery_open",
                "url": url,
                "retailer": self.code,
                "discovery_name": discovery_name,
                "tier": tier,
                "surface": surface,
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
                    "tier": tier,
                    "surface": surface,
                },
            )
            await session.screenshot(page, label=f"ml_discovery_{blocked}")
            return 0, 0

        max_pages = int(limits.get("max_pages_per_query") or 1)
        for page_idx in range(max_pages):
            for _ in range(3):
                await page.mouse.wheel(0, 2200)
                await page.wait_for_timeout(800)

            batch = await extract_listings_from_page(page, category_raw=category_raw)
            page_kept = 0
            for cand in batch:
                result = classify_mercadolibre_product(
                    title=cand.title,
                    category_raw=category_raw,
                    discovery_name=str(discovery_name) if discovery_name else None,
                )
                if not is_collection_eligible(result):
                    irrelevant_seen += 1
                    continue
                if title_looks_irrelevant(cand.title):
                    irrelevant_seen += 1
                    continue
                cand.raw = {
                    **(cand.raw or {}),
                    "discovery_name": discovery_name,
                    "discovery_tier": tier,
                    "discovery_surface": surface,
                    "product_type_hint": entry.get("product_type_hint"),
                    "classification_preview": result.to_dict(),
                }
                collected.append(cand)
                kept += 1
                page_kept += 1

            logger.info(
                "mercadolibre_listing_batch",
                extra={
                    "event": "mercadolibre_listing_batch",
                    "url": url,
                    "page_idx": page_idx,
                    "count": len(batch),
                    "kept": page_kept,
                    "irrelevant_seen": irrelevant_seen,
                    "tier": tier,
                    "retailer": self.code,
                },
            )
            await session.screenshot(page, label=f"ml_listing_{tier}")
            if len(dedupe_candidates(collected)) >= card_budget:
                break

            if page_idx + 1 >= max_pages:
                break
            next_clicked = False
            for sel in (
                "li.andes-pagination__button--next a",
                "a.andes-pagination__link[title='Seguinte']",
                "a.andes-pagination__link[title='Siguiente']",
                "a.andes-pagination__link[title='Next']",
            ):
                loc = page.locator(sel)
                try:
                    if await loc.count():
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(2000)
                        next_clicked = True
                        break
                except Exception:  # noqa: BLE001
                    continue
            if not next_clicked:
                break

        await asyncio.sleep(0.8)
        return kept, irrelevant_seen

    async def discover_listings(
        self,
        session: BrowserSession,
        *,
        limit: int,
    ) -> list[ListingCandidate]:
        """Discover unique in-scope listing candidates.

        Primary search/category surfaces first; ofertas only as secondary
        enrichment when primary yield is insufficient.
        """
        page = await session.new_page()
        collected: list[ListingCandidate] = []
        irrelevant_total = 0
        limits = self.discovery.get("limits") or {}
        overscan = int(limits.get("discovery_overscan_factor") or 4)
        secondary_after = int(limits.get("secondary_after_unique") or 8)
        target = max(limit, 1)
        card_budget = max(target * overscan, target)
        try:
            entries = self._discovery_entries()
            primary_entries = [e for t, e in entries if t == "primary"]
            secondary_entries = [e for t, e in entries if t in {"secondary", "legacy"}]

            for entry in primary_entries:
                if len(dedupe_candidates(collected)) >= card_budget:
                    break
                _kept, irr = await self._harvest_entry(
                    session,
                    page,
                    entry,
                    tier="primary",
                    card_budget=card_budget,
                    collected=collected,
                )
                irrelevant_total += irr

            unique_primary = len(dedupe_candidates(collected))
            need_secondary = unique_primary < max(secondary_after, target)
            if need_secondary:
                logger.info(
                    "mercadolibre_discovery_secondary",
                    extra={
                        "event": "mercadolibre_discovery_secondary",
                        "unique_primary": unique_primary,
                        "secondary_after": secondary_after,
                        "retailer": self.code,
                    },
                )
                for entry in secondary_entries:
                    if len(dedupe_candidates(collected)) >= card_budget:
                        break
                    _kept, irr = await self._harvest_entry(
                        session,
                        page,
                        entry,
                        tier="secondary",
                        card_budget=card_budget,
                        collected=collected,
                    )
                    irrelevant_total += irr
        finally:
            await page.close()

        unique = dedupe_candidates(collected)
        logger.info(
            "mercadolibre_discovery_summary",
            extra={
                "event": "mercadolibre_discovery_summary",
                "unique_candidates": len(unique),
                "irrelevant_skipped": irrelevant_total,
                "target_valid": target,
                "retailer": self.code,
            },
        )
        return unique[:card_budget]

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
            if candidate.raw:
                product.raw_payload = {
                    **(product.raw_payload or {}),
                    **{
                        k: v
                        for k, v in candidate.raw.items()
                        if k
                        in {
                            "discovery_name",
                            "discovery_tier",
                            "discovery_surface",
                            "product_type_hint",
                        }
                    },
                }
            await session.screenshot(
                page, label=f"ml_product_{product.retailer_sku}"
            )
            await asyncio.sleep(0.5)
            return product
        finally:
            await page.close()


def build_collector() -> MercadoLibreCollector:
    return MercadoLibreCollector()
