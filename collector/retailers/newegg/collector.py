"""Newegg US retailer collector adapter."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import urlencode

from collector.base import ListingCandidate, RetailerCollector
from collector.browser import BrowserSession
from collector.config_loader import get_retailer
from collector.normalize import NormalizedProduct
from collector.retailers.newegg.discovery import load_discovery_config
from collector.retailers.newegg.listing import extract_listings_from_page
from collector.retailers.newegg.product_page import (
    build_from_listing,
    is_bot_challenge,
    parse_product_page,
)
from collector.universe_config import (
    allocate_stratum_budgets,
    load_search_universe_config,
    stamp_stratum_candidate,
)

logger = logging.getLogger("collector.newegg")


def _newegg_search_url(query: str, *, page: int = 1) -> str:
    params = {"d": query}
    if page > 1:
        params["page"] = str(page)
    return f"https://www.newegg.com/p/pl?{urlencode(params)}"


class NeweggCollector(RetailerCollector):
    code = "newegg"
    uses_observed_result_limit = True

    def __init__(self) -> None:
        cfg = get_retailer("newegg")
        self.country_code = cfg["country_code"]
        self.currency = cfg["currency"]
        self.base_url = cfg["base_url"]
        self.discovery = load_discovery_config()
        self.discovery_stats: dict = {}
        self.stratum_filter: str | None = None

    async def _wait_for_listings_or_raise(self, page, session: BrowserSession) -> None:
        """Allow Cloudflare/Newegg challenges a short window to clear before failing."""
        for attempt in range(1, 13):
            try:
                content = await page.content()
                title = await page.title()
                item_count = await page.locator(
                    "a.item-title, .item-cell, a[href*='/p/']"
                ).count()
            except Exception:  # noqa: BLE001 - navigation races; not a bypass
                await page.wait_for_timeout(1500)
                continue
            challenged = is_bot_challenge(content) or is_bot_challenge(title)
            if item_count > 0 and not challenged:
                return
            if challenged:
                logger.warning(
                    "newegg_challenge_wait",
                    extra={
                        "event": "newegg_challenge_wait",
                        "attempt": attempt,
                        "url": page.url,
                        "retailer": self.code,
                    },
                )
                await page.wait_for_timeout(2500)
                continue
            await page.wait_for_timeout(1000)

        path = await session.screenshot(page, label="newegg_bot_challenge_listing")
        raise RuntimeError(
            "Newegg blocked or failed listing discovery (bot challenge / empty results). "
            "If this persists, start Chrome with --remote-debugging-port=9222 and set "
            f"COLLECTION_CDP_URL=http://127.0.0.1:9222. screenshot={path}"
        )

    async def _page_blocked(self, page) -> Optional[str]:
        try:
            content = await page.content()
            title = await page.title()
        except Exception:  # noqa: BLE001
            return None
        if is_bot_challenge(content) or is_bot_challenge(title):
            return "bot_challenge"
        return None

    async def _has_next_page(self, page, current_page: int) -> bool:
        next_loc = page.locator(
            "button[title*='Next' i], a[title*='Next' i], "
            ".list-tool-pagination button.btn:has-text('>'), "
            f"a[href*='page={current_page + 1}']"
        )
        try:
            if await next_loc.count() <= 0:
                return False
            first = next_loc.first
            disabled = await first.get_attribute("disabled")
            aria = await first.get_attribute("aria-disabled")
            classes = (await first.get_attribute("class")) or ""
            if disabled is not None or aria == "true" or "disabled" in classes.lower():
                return False
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _harvest_stratum(
        self,
        session: BrowserSession,
        page,
        *,
        query: str,
        url_template: str,
        category_raw: Optional[str],
        budget: int,
        max_pages: int,
        stop_on_empty_page: bool,
    ) -> tuple[list[ListingCandidate], dict]:
        """Observe one generic Newegg query up to ``budget`` native positions."""
        collected: list[ListingCandidate] = []
        stats = {
            "pages_attempted": 0,
            "pages_inspected": 0,
            "pages_blocked": 0,
            "pagination_reliable": True,
            "last_observed_position": 0,
            "search_status": "OK",
            "query": query,
            "search_url": url_template or _newegg_search_url(query, page=1),
            "stop_reason": None,
            "ranked_search_ok": True,
            "used_fallback": False,
        }
        if budget <= 0:
            stats["stop_reason"] = "zero_budget"
            return collected, stats

        for page_number in range(1, max_pages + 1):
            if len(collected) >= budget:
                stats["stop_reason"] = "requested_depth"
                break
            url = _newegg_search_url(query, page=page_number)
            stats["pages_attempted"] += 1
            logger.info(
                "newegg_discovery_open",
                extra={
                    "event": "newegg_discovery_open",
                    "url": url,
                    "page": page_number,
                    "query": query,
                    "retailer": self.code,
                },
            )
            await session.goto(page, url)
            try:
                await self._wait_for_listings_or_raise(page, session)
            except RuntimeError:
                blocked = await self._page_blocked(page)
                stats["pages_blocked"] += 1
                stats["pagination_reliable"] = False
                stats["ranked_search_ok"] = False
                if page_number == 1:
                    stats["search_status"] = "BLOCKED"
                    stats["stop_reason"] = blocked or "bot_challenge"
                    await session.screenshot(
                        page, label=f"newegg_discovery_{stats['stop_reason']}"
                    )
                    return collected, stats
                stats["stop_reason"] = "pagination_blocked"
                break

            blocked = await self._page_blocked(page)
            if blocked:
                stats["pages_blocked"] += 1
                stats["pagination_reliable"] = False
                stats["ranked_search_ok"] = page_number > 1
                if page_number == 1:
                    stats["search_status"] = "BLOCKED"
                    stats["ranked_search_ok"] = False
                stats["stop_reason"] = blocked
                await session.screenshot(page, label=f"newegg_discovery_{blocked}")
                break

            await page.mouse.wheel(0, 2400)
            await page.wait_for_timeout(1000)
            batch = await extract_listings_from_page(page, category_raw=category_raw)
            logger.info(
                "newegg_listing_batch",
                extra={
                    "event": "newegg_listing_batch",
                    "url": url,
                    "page": page_number,
                    "count": len(batch),
                    "query": query,
                    "retailer": self.code,
                },
            )
            await session.screenshot(page, label=f"newegg_listing_{query[:24]}_p{page_number}")
            if not batch:
                stats["stop_reason"] = (
                    "empty_page" if page_number == 1 else "empty_later_page"
                )
                if stop_on_empty_page:
                    break
                continue

            stats["pages_inspected"] += 1
            page_skus = [
                (c.retailer_sku or "").strip() for c in batch if (c.retailer_sku or "").strip()
            ]
            prior_skus = {
                (c.retailer_sku or "").strip()
                for c in collected
                if (c.retailer_sku or "").strip()
            }
            if page_number > 1 and page_skus:
                overlap = sum(1 for sku in page_skus if sku in prior_skus) / len(page_skus)
                stats["page_overlap_ratio"] = round(overlap, 3)
                if overlap >= 0.85:
                    stats["pagination_reliable"] = False
                    stats["stop_reason"] = "page_overlap_unreliable"
            for cand in batch:
                if len(collected) >= budget:
                    break
                sku = (cand.retailer_sku or "").strip()
                if sku and sku in prior_skus:
                    cand.raw = {**(cand.raw or {}), "repeat_promotion": True}
                cand.search_page = page_number
                collected.append(cand)

            if stats.get("stop_reason") == "page_overlap_unreliable":
                break
            if len(collected) >= budget:
                stats["stop_reason"] = "requested_depth"
                break
            has_next = await self._has_next_page(page, page_number)
            if not has_next:
                stats["stop_reason"] = "no_next_page"
                break
            await asyncio.sleep(1.0)
        else:
            stats["pagination_reliable"] = False
            stats["stop_reason"] = stats["stop_reason"] or "max_pages_reached"

        stats["last_observed_position"] = len(collected)
        return collected, stats

    async def discover_listings(
        self,
        session: BrowserSession,
        *,
        limit: int,
    ) -> list[ListingCandidate]:
        """Observe stratified generic SERPs until each stratum budget or exhaustion."""
        universe = load_search_universe_config()
        limits = self.discovery.get("limits") or {}
        max_pages = int(limits.get("max_pages_per_query") or universe.max_pages)
        allocated = allocate_stratum_budgets(
            self.code, total_limit=limit, stratum_filter=self.stratum_filter
        )
        collected: list[ListingCandidate] = []
        stratum_reports: list[dict] = []
        pages_attempted = 0
        pages_inspected = 0
        pages_blocked = 0
        pagination_reliable = True
        queries: list[str] = []
        universe_slot = 0
        page = await session.new_page()
        try:
            for spec, budget in allocated:
                batch, stats = await self._harvest_stratum(
                    session,
                    page,
                    query=spec.query,
                    url_template=spec.url,
                    category_raw=spec.name,
                    budget=budget,
                    max_pages=max_pages,
                    stop_on_empty_page=universe.stop_on_empty_page,
                )
                queries.append(spec.query)
                pages_attempted += int(stats.get("pages_attempted") or 0)
                pages_inspected += int(stats.get("pages_inspected") or 0)
                pages_blocked += int(stats.get("pages_blocked") or 0)
                pagination_reliable = pagination_reliable and bool(
                    stats.get("pagination_reliable", True)
                )
                native = 0
                for cand in batch:
                    native += 1
                    universe_slot += 1
                    stamp_stratum_candidate(
                        cand,
                        stratum=spec.name,
                        query=spec.query,
                        search_position=native,
                        search_page=int(cand.search_page or 1),
                        universe_slot=universe_slot,
                        surface="search",
                        product_type_hint=spec.product_type_hint,
                    )
                    cand.stratum = spec.name
                    cand.universe_slot = universe_slot
                    collected.append(cand)
                observed = native
                from collector.observation import stratum_completeness

                completeness = stratum_completeness(
                    requested=budget,
                    observed=observed,
                    ranked_search_ok=bool(stats.get("ranked_search_ok", True)),
                    used_fallback=False,
                    search_blocked=str(stats.get("search_status") or "") == "BLOCKED",
                )
                stats["last_observed_position"] = observed
                stratum_reports.append(
                    {
                        "stratum": spec.name,
                        "query": spec.query,
                        "requested": budget,
                        "observed": observed,
                        "completeness": completeness,
                        "pages_attempted": stats.get("pages_attempted", 0),
                        "pages_inspected": stats.get("pages_inspected", 0),
                        "pages_blocked": stats.get("pages_blocked", 0),
                        "pagination_reliable": stats.get("pagination_reliable", True),
                        "last_observed_position": observed,
                        "search_status": stats.get("search_status", "OK"),
                        "stop_reason": stats.get("stop_reason"),
                        "search_url": stats.get("search_url"),
                        "ranked_search_ok": stats.get("ranked_search_ok", True),
                        "used_fallback": False,
                        "ranking_scope": "stratum_query",
                    }
                )
        finally:
            await page.close()

        blocked_all = bool(stratum_reports) and all(
            str(item.get("search_status")) == "BLOCKED" for item in stratum_reports
        )
        self.discovery_stats = {
            "pages_attempted": pages_attempted,
            "pages_inspected": pages_inspected,
            "pages_blocked": pages_blocked,
            "pagination_reliable": pagination_reliable,
            "last_observed_position": max(
                (int(item.get("last_observed_position") or 0) for item in stratum_reports),
                default=0,
            ),
            "search_status": "BLOCKED" if blocked_all else "OK",
            "query": queries[0] if len(queries) == 1 else queries,
            "queries": queries,
            "stop_reason": None
            if all(item.get("completeness") == "COMPLETE" for item in stratum_reports)
            else "stratum_incomplete",
            "strata": stratum_reports,
            "universe_slots": universe_slot,
        }
        return collected

    async def fetch_product(
        self,
        session: BrowserSession,
        candidate: ListingCandidate,
    ) -> NormalizedProduct:
        page = await session.new_page()
        try:
            await session.goto(page, candidate.source_url)
            for attempt in range(1, 9):
                content = await page.content()
                title = await page.title()
                if not (is_bot_challenge(content) or is_bot_challenge(title)):
                    break
                logger.warning(
                    "newegg_product_challenge_wait",
                    extra={
                        "event": "newegg_product_challenge_wait",
                        "attempt": attempt,
                        "sku": candidate.retailer_sku,
                        "url": candidate.source_url,
                        "retailer": self.code,
                    },
                )
                await page.wait_for_timeout(2500)
            else:
                path = await session.screenshot(
                    page, label=f"newegg_bot_{candidate.retailer_sku}"
                )
                logger.warning(
                    "newegg_pdp_blocked",
                    extra={
                        "event": "newegg_pdp_blocked",
                        "reason": "bot_challenge",
                        "sku": candidate.retailer_sku,
                        "url": candidate.source_url,
                        "screenshot": path,
                        "retailer": self.code,
                    },
                )
                listing_raw = dict(candidate.raw or {})
                listing_raw.setdefault("title", candidate.title)
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
                    detail_page_status="bot_challenge",
                    listing_raw=listing_raw,
                )

            listing_raw = dict(candidate.raw or {})
            listing_raw.setdefault("title", candidate.title)
            listing_raw.setdefault("price_text", candidate.price_text)
            listing_raw.setdefault("list_price_text", candidate.list_price_text)
            listing_raw.setdefault("promo_text", candidate.promo_text)
            listing_raw.setdefault("sku", candidate.retailer_sku)
            listing_raw.setdefault("href", candidate.source_url)
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
                listing_features=candidate.raw.get("features")
                if isinstance(candidate.raw.get("features"), list)
                else None,
                listing_raw=listing_raw,
            )
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
