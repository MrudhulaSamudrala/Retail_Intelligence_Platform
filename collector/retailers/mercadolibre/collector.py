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
from collector.retailers.mercadolibre.listing import extract_listings_from_page
from collector.retailers.mercadolibre.product_page import (
    build_from_listing,
    is_account_verification,
    is_bot_challenge,
    parse_product_page,
)
from collector.retailers.mercadolibre.layers import is_network_payload_useful
from collector.retailers.mercadolibre.relevance import is_in_collection_scope
from collector.universe_config import (
    allocate_stratum_budgets,
    generic_query_for,
    load_search_universe_config,
    stamp_stratum_candidate,
)

logger = logging.getLogger("collector.mercadolibre")


class MercadoLibreCollector(RetailerCollector):
    code = "mercadolibre"
    uses_observed_result_limit = True

    def __init__(self) -> None:
        cfg = get_retailer("mercadolibre")
        self.country_code = cfg["country_code"]
        self.currency = cfg["currency"]
        self.base_url = cfg["base_url"]
        self.discovery = load_discovery_config()
        self.allow_listing_only = bool(
            self.discovery.get("allow_listing_only_fallback", True)
        )
        self.discovery_stats: dict[str, Any] = {}
        self.stratum_filter: str | None = None

    def build_browser_session(self) -> BrowserSession:
        """Portuguese locale; do not enable Chrome Translate."""
        return BrowserSession(
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5"},
            languages=["pt-BR", "pt"],
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
        try:
            content = await page.content()
            title = await page.title()
            url = page.url
        except Exception:  # noqa: BLE001 - navigation races; do not invent a block
            return None
        if is_account_verification(content, url) or is_account_verification(title, url):
            return "account_verification"
        if is_bot_challenge(content) or is_bot_challenge(title):
            return "bot_challenge"
        return None

    def _entries_for_stratum(
        self, stratum: str, *, tier: str
    ) -> list[dict[str, Any]]:
        key = "discovery_primary" if tier == "primary" else "discovery_secondary"
        out: list[dict[str, Any]] = []
        for item in self.discovery.get(key) or []:
            name = str(item.get("stratum") or item.get("name") or "").lower()
            if name == stratum or str(item.get("stratum") or "").lower() == stratum:
                out.append(item)
        return out

    def _empty_discovery_stats(self, *, query: str, url: str) -> dict[str, Any]:
        return {
            "pages_attempted": 0,
            "pages_inspected": 0,
            "pages_blocked": 0,
            "pagination_reliable": True,
            "last_observed_position": 0,
            "search_status": "OK",
            "query": query,
            "search_url": url,
            "stop_reason": None,
        }

    async def _harvest_entry(
        self,
        session: BrowserSession,
        page,
        entry: dict[str, Any],
        *,
        tier: str,
        card_budget: int,
        collected: list[ListingCandidate],
        stats: dict[str, Any],
    ) -> tuple[int, int]:
        """Harvest one discovery URL. Returns (kept, excluded_classified).

        Ineligible titles are still kept — they consume observation slots.
        ``card_budget`` is this harvest's native-position budget (stratum).
        """
        universe = load_search_universe_config()
        limits = self.discovery.get("limits") or {}
        url = entry["url"]
        discovery_name = entry.get("name")
        query = str(entry.get("query") or generic_query_for(self.code))
        surface = entry.get("surface") or tier
        category_raw = entry.get("category_label") or None
        excluded_seen = 0
        kept = 0
        stats["query"] = query
        stats["search_url"] = url
        stats["discovery_surface"] = surface
        stats["ranked_search_ok"] = True
        stats["used_fallback"] = tier == "secondary"

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
        stats["pages_attempted"] += 1
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
            stats["pages_blocked"] += 1
            stats["pagination_reliable"] = False
            stats["ranked_search_ok"] = False
            if tier == "primary":
                stats["search_status"] = "BLOCKED"
            stats["stop_reason"] = blocked
            return 0, 0

        max_pages = int(limits.get("max_pages_per_query") or universe.max_pages)
        for page_idx in range(max_pages):
            if page_idx > 0:
                stats["pages_attempted"] += 1
            for _ in range(3):
                await page.mouse.wheel(0, 2200)
                await page.wait_for_timeout(800)

            page_blocked = await self._page_blocked(page)
            if page_blocked:
                stats["pages_blocked"] += 1
                stats["pagination_reliable"] = False
                stats["stop_reason"] = page_blocked
                if page_idx == 0 and kept == 0:
                    stats["search_status"] = "BLOCKED"
                    stats["ranked_search_ok"] = False
                break

            batch = await extract_listings_from_page(page, category_raw=category_raw)
            page_kept = 0
            for cand in batch:
                if kept >= card_budget:
                    break
                result = classify_mercadolibre_product(
                    title=cand.title,
                    category_raw=category_raw,
                    discovery_name=str(discovery_name) if discovery_name else None,
                )
                if not is_collection_eligible(result):
                    excluded_seen += 1
                native_position = kept + 1
                cand.search_position = native_position
                cand.search_page = page_idx + 1
                cand.query = query
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
                    "excluded_observed": excluded_seen,
                    "tier": tier,
                    "retailer": self.code,
                },
            )
            await session.screenshot(page, label=f"ml_listing_{tier}_p{page_idx + 1}")
            if page_kept == 0:
                stats["stop_reason"] = "empty_page" if page_idx == 0 else "empty_later_page"
                if universe.stop_on_empty_page:
                    break
            else:
                stats["pages_inspected"] += 1
            if kept >= card_budget:
                stats["stop_reason"] = "requested_depth"
                break

            if page_idx + 1 >= max_pages:
                if kept < card_budget:
                    stats["pagination_reliable"] = False
                    stats["stop_reason"] = stats["stop_reason"] or "max_pages_reached"
                break
            next_clicked = False
            next_present = False
            for sel in (
                "li.andes-pagination__button--next a",
                "a.andes-pagination__link[title='Seguinte']",
                "a.andes-pagination__link[title='Siguiente']",
                "a.andes-pagination__link[title='Next']",
            ):
                loc = page.locator(sel)
                try:
                    if await loc.count():
                        next_present = True
                        await loc.first.click(timeout=2000)
                        await page.wait_for_timeout(2000)
                        next_clicked = True
                        break
                except Exception:  # noqa: BLE001
                    stats["pagination_reliable"] = False
                    stats["stop_reason"] = "pagination_click_failed"
                    continue
            if next_present and not next_clicked:
                stats["pagination_reliable"] = False
                stats["stop_reason"] = "pagination_unreliable"
                break
            if not next_clicked:
                stats["stop_reason"] = stats["stop_reason"] or "no_next_page"
                break

        await asyncio.sleep(0.8)
        return kept, excluded_seen

    async def discover_listings(
        self,
        session: BrowserSession,
        *,
        limit: int,
    ) -> list[ListingCandidate]:
        """Discover listing candidates per stratum in native SERP order.

        ``limit`` is the total observation target across strata.
        Ineligible products are kept. Secondary surfaces open only when
        that stratum's ranked search yields zero observable results.
        Fallback never makes the stratum COMPLETE.
        """
        page = await session.new_page()
        collected: list[ListingCandidate] = []
        excluded_total = 0
        allocated = allocate_stratum_budgets(
            self.code, total_limit=limit, stratum_filter=self.stratum_filter
        )
        stratum_reports: list[dict[str, Any]] = []
        pages_attempted = 0
        pages_inspected = 0
        pages_blocked = 0
        pagination_reliable = True
        queries: list[str] = []
        universe_slot = 0
        try:
            for spec, budget in allocated:
                queries.append(spec.query)
                harvest_stats = self._empty_discovery_stats(query=spec.query, url=spec.url)
                harvest_stats["ranked_search_ok"] = True
                harvest_stats["used_fallback"] = False
                before = len(collected)
                primary_entries = self._entries_for_stratum(spec.name, tier="primary")
                if not primary_entries:
                    primary_entries = [
                        {
                            "name": spec.name,
                            "stratum": spec.name,
                            "surface": "search",
                            "query": spec.query,
                            "url": spec.url,
                            "product_type_hint": spec.product_type_hint,
                            "category_label": f"search:{spec.query}",
                        }
                    ]
                _kept, excl = await self._harvest_entry(
                    session,
                    page,
                    primary_entries[0],
                    tier="primary",
                    card_budget=budget,
                    collected=collected,
                    stats=harvest_stats,
                )
                excluded_total += excl
                used_fallback = False
                ranked_ok = bool(harvest_stats.get("ranked_search_ok", True)) and str(
                    harvest_stats.get("search_status") or ""
                ) != "BLOCKED"
                if len(collected) == before:
                    secondary_entries = self._entries_for_stratum(spec.name, tier="secondary")
                    if spec.fallback_url and not secondary_entries:
                        secondary_entries = [
                            {
                                "name": f"{spec.name}_ofertas",
                                "stratum": spec.name,
                                "surface": "ofertas",
                                "query": spec.query,
                                "url": spec.fallback_url,
                                "product_type_hint": spec.product_type_hint,
                                "category_label": "ofertas_query",
                            }
                        ]
                    if secondary_entries:
                        logger.info(
                            "mercadolibre_discovery_secondary",
                            extra={
                                "event": "mercadolibre_discovery_secondary",
                                "stratum": spec.name,
                                "reason": "primary_zero_observable",
                                "retailer": self.code,
                            },
                        )
                        fallback_stats = self._empty_discovery_stats(
                            query=spec.query, url=str(secondary_entries[0].get("url") or "")
                        )
                        _kept, excl = await self._harvest_entry(
                            session,
                            page,
                            secondary_entries[0],
                            tier="secondary",
                            card_budget=budget,
                            collected=collected,
                            stats=fallback_stats,
                        )
                        excluded_total += excl
                        used_fallback = len(collected) > before
                        harvest_stats["pages_attempted"] = int(
                            harvest_stats.get("pages_attempted") or 0
                        ) + int(fallback_stats.get("pages_attempted") or 0)
                        harvest_stats["pages_inspected"] = int(
                            harvest_stats.get("pages_inspected") or 0
                        ) + int(fallback_stats.get("pages_inspected") or 0)
                        harvest_stats["pages_blocked"] = int(
                            harvest_stats.get("pages_blocked") or 0
                        ) + int(fallback_stats.get("pages_blocked") or 0)
                        harvest_stats["pagination_reliable"] = False
                        harvest_stats["used_fallback"] = used_fallback
                        harvest_stats["fallback_surface"] = "ofertas"
                        harvest_stats["stop_reason"] = harvest_stats.get(
                            "stop_reason"
                        ) or fallback_stats.get("stop_reason")

                batch = collected[before:]
                native = 0
                for cand in batch:
                    native += 1
                    universe_slot += 1
                    stamp_stratum_candidate(
                        cand,
                        stratum=spec.name,
                        query=spec.query,
                        search_position=int(cand.search_position or native),
                        search_page=int(cand.search_page or 1),
                        universe_slot=universe_slot,
                        surface="ofertas" if used_fallback else "search",
                        used_fallback=used_fallback,
                        product_type_hint=spec.product_type_hint,
                    )
                    cand.stratum = spec.name
                    cand.universe_slot = universe_slot
                    cand.raw = {
                        **(cand.raw or {}),
                        "ranked_search": not used_fallback,
                    }
                pages_attempted += int(harvest_stats.get("pages_attempted") or 0)
                pages_inspected += int(harvest_stats.get("pages_inspected") or 0)
                pages_blocked += int(harvest_stats.get("pages_blocked") or 0)
                pagination_reliable = pagination_reliable and bool(
                    harvest_stats.get("pagination_reliable", True)
                ) and not used_fallback
                from collector.observation import stratum_completeness

                completeness = stratum_completeness(
                    requested=budget,
                    observed=native,
                    ranked_search_ok=ranked_ok,
                    used_fallback=used_fallback,
                    search_blocked=str(harvest_stats.get("search_status") or "")
                    == "BLOCKED",
                )
                stratum_reports.append(
                    {
                        "stratum": spec.name,
                        "query": spec.query,
                        "requested": budget,
                        "observed": native,
                        "completeness": completeness,
                        "pages_attempted": harvest_stats.get("pages_attempted", 0),
                        "pages_inspected": harvest_stats.get("pages_inspected", 0),
                        "pages_blocked": harvest_stats.get("pages_blocked", 0),
                        "pagination_reliable": harvest_stats.get(
                            "pagination_reliable", True
                        )
                        and not used_fallback,
                        "last_observed_position": native,
                        "search_status": harvest_stats.get("search_status", "OK"),
                        "stop_reason": harvest_stats.get("stop_reason"),
                        "search_url": harvest_stats.get("search_url") or spec.url,
                        "ranked_search_ok": ranked_ok,
                        "used_fallback": used_fallback,
                        "ranking_scope": "stratum_query",
                        "discovery_surface": "ofertas" if used_fallback else "search",
                    }
                )
        finally:
            await page.close()

        blocked_all = bool(stratum_reports) and all(
            str(item.get("search_status")) == "BLOCKED" and not item.get("used_fallback")
            for item in stratum_reports
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
        logger.info(
            "mercadolibre_discovery_summary",
            extra={
                "event": "mercadolibre_discovery_summary",
                "observable_candidates": len(collected),
                "excluded_classified": excluded_total,
                "observation_target": limit,
                "strata": [item["stratum"] for item in stratum_reports],
                "search_status": self.discovery_stats.get("search_status"),
                "retailer": self.code,
            },
        )
        return collected

    async def fetch_product(
        self,
        session: BrowserSession,
        candidate: ListingCandidate,
    ) -> NormalizedProduct:
        page = await session.new_page()
        network_payloads: list[Any] = []

        async def _capture_response(response) -> None:
            try:
                url = response.url
                headers = response.headers or {}
                ct = headers.get("content-type") or headers.get("Content-Type") or ""
                if not is_network_payload_useful(url, ct):
                    return
                body = await response.json()
                if body is not None:
                    network_payloads.append(body)
            except Exception:  # noqa: BLE001
                return

        page.on("response", _capture_response)
        listing_raw = dict(candidate.raw or {})
        listing_raw.setdefault("title", candidate.title)
        listing_raw.setdefault("price_text", candidate.price_text)
        listing_raw.setdefault("list_price_text", candidate.list_price_text)
        listing_raw.setdefault("promo_text", candidate.promo_text)
        listing_raw.setdefault("sku", candidate.retailer_sku)
        listing_raw.setdefault("href", candidate.source_url)

        def _finish(prod: NormalizedProduct) -> NormalizedProduct:
            try:
                from collector.retailers.mercadolibre.api.enrich import enrich_product

                return enrich_product(prod)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mercadolibre_api_enrich_skipped",
                    extra={
                        "event": "mercadolibre_api_enrich_skipped",
                        "sku": prod.retailer_sku,
                        "error": str(exc),
                    },
                )
                payload = dict(prod.raw_payload or {})
                payload["api_status"] = "API_UNAVAILABLE"
                payload["api_error"] = str(exc)
                prod.raw_payload = payload
                return prod

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
                return _finish(
                    build_from_listing(
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
                    extra_raw=listing_raw,
                    )
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
                    listing_raw=listing_raw,
                    network_payloads=network_payloads,
                )
            except RuntimeError as exc:
                if self.allow_listing_only and "verification" in str(exc).lower():
                    return _finish(
                        build_from_listing(
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
                        extra_raw=listing_raw,
                        )
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
                            "stratum",
                            "universe_slot",
                            "ranking_scope",
                            "used_fallback",
                            "ranked_search",
                        }
                    },
                }
            await session.screenshot(
                page, label=f"ml_product_{product.retailer_sku}"
            )
            await asyncio.sleep(0.5)
            return _finish(product)
        finally:
            await page.close()


def build_collector() -> MercadoLibreCollector:
    return MercadoLibreCollector()
