"""Common collection pipeline shared by all retailer adapters."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from collector.base import CollectionOutcome, RetailerCollector
from collector.browser import BrowserSession
from collector.persist import CollectionPersister

logger = logging.getLogger("collector.pipeline")


def _compliance_persist_eligible(product) -> bool:
    """Persist S1–P5 only for eligible computing types (shared classifier)."""
    from collector.retailers.mercadolibre.classification import (
        EXCLUDED,
        OTHER_TYPE,
        SUPPORTED_PRODUCT_TYPES,
        classify_mercadolibre_product,
        is_collection_eligible,
    )

    stored = (getattr(product, "product_type", None) or "").strip().lower()
    if stored == OTHER_TYPE:
        return False
    classified = classify_mercadolibre_product(
        title=getattr(product, "title", None),
        category_raw=None,
    )
    if classified.status == EXCLUDED or classified.hard_negative:
        return False
    if classified.product_type == OTHER_TYPE:
        return False
    if stored in SUPPORTED_PRODUCT_TYPES:
        return True
    return is_collection_eligible(classified)


class CollectionPipeline:
    """Discover → enrich → normalize (adapter) → dedupe → persist."""

    def __init__(self, session: Session, collector: RetailerCollector) -> None:
        self.session = session
        self.collector = collector
        self.persister = CollectionPersister(session)

    async def run(self, *, limit: int = 20) -> CollectionOutcome:
        outcome = CollectionOutcome()
        seen_skus: set[str] = set()
        run = self.persister.start_run(
            retailer_code=self.collector.code,
            country_code=self.collector.country_code,
            run_type="pricing",
            limit=limit,
        )
        self.session.commit()
        run_id = run.id
        error_message: Optional[str] = None

        logger.info(
            "collection_started",
            extra={
                "event": "collection_started",
                "retailer": self.collector.code,
                "country": self.collector.country_code,
                "run_id": run_id,
                "count": limit,
            },
        )

        observed_universe = bool(
            getattr(self.collector, "uses_observed_result_limit", False)
        )
        outcome.requested = limit
        discovery_limit = limit if observed_universe else limit * 2

        try:
            async with self._browser_session() as browser:
                candidates = await self.collector.discover_listings(
                    browser, limit=discovery_limit
                )
                outcome.discovered = len(candidates)
                logger.info(
                    "listings_discovered",
                    extra={
                        "event": "listings_discovered",
                        "retailer": self.collector.code,
                        "run_id": run_id,
                        "count": len(candidates),
                    },
                )

                if observed_universe:
                    await self._collect_observed_universe(
                        browser,
                        candidates,
                        outcome=outcome,
                        limit=limit,
                        run_id=run_id,
                        seen_skus=seen_skus,
                    )
                else:
                    for candidate in candidates:
                        if len(outcome.success) >= limit:
                            break

                        sku = candidate.retailer_sku.strip()
                        if not sku:
                            outcome.failed.append(
                                {"url": candidate.source_url, "error": "missing_sku"}
                            )
                            continue
                        if sku in seen_skus:
                            outcome.skipped_duplicates.append(sku)
                            continue
                        seen_skus.add(sku)

                        try:
                            product = await self.collector.fetch_product(
                                browser, candidate
                            )
                            if not self.collector.is_in_collection_scope(product):
                                outcome.skipped_irrelevant.append(
                                    {
                                        "sku": sku,
                                        "url": candidate.source_url,
                                        "title": product.title,
                                        "product_type": product.product_type,
                                        "reason": "out_of_collection_scope",
                                    }
                                )
                                logger.info(
                                    "product_skipped_irrelevant",
                                    extra={
                                        "event": "product_skipped_irrelevant",
                                        "retailer": self.collector.code,
                                        "run_id": run_id,
                                        "sku": sku,
                                        "product_type": product.product_type,
                                    },
                                )
                                continue
                            observed_at = datetime.now(timezone.utc)
                            product_id = self.persister.save_product(
                                product,
                                collection_run_id=run_id,
                                observed_at=observed_at,
                            )
                            self._persist_surface_evidence(
                                product,
                                product_id=product_id,
                                collection_run_id=run_id,
                                observed_at=observed_at,
                            )
                            self.session.commit()
                            outcome.success.append(product)
                        except Exception as exc:  # noqa: BLE001 - per-product isolation
                            self.session.rollback()
                            logger.exception(
                                "product_failed",
                                extra={
                                    "event": "product_failed",
                                    "retailer": self.collector.code,
                                    "run_id": run_id,
                                    "sku": sku,
                                    "url": candidate.source_url,
                                    "error": str(exc),
                                },
                            )
                            outcome.failed.append(
                                {
                                    "sku": sku,
                                    "url": candidate.source_url,
                                    "error": str(exc),
                                }
                            )
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            if "bot challenge" in error_message.lower() or "unusual traffic" in error_message.lower():
                outcome.bot_blocked = True
            logger.exception(
                "collection_aborted",
                extra={
                    "event": "collection_aborted",
                    "retailer": self.collector.code,
                    "run_id": run_id,
                    "error": error_message,
                },
            )

        if observed_universe:
            from collector.observation import (
                completeness_status,
                overall_completeness_from_strata,
                run_status_from_completeness,
            )

            stats = dict(getattr(self.collector, "discovery_stats", None) or {})
            search_blocked = str(stats.get("search_status") or "") == "BLOCKED"
            strata = list(
                (outcome.universe or {}).get("strata") or stats.get("strata") or []
            )
            if strata:
                completeness = overall_completeness_from_strata(
                    strata,
                    requested=limit,
                    observed=outcome.observed,
                    had_error=bool(error_message) or outcome.bot_blocked,
                    search_blocked=search_blocked or outcome.bot_blocked,
                )
            else:
                completeness = completeness_status(
                    requested=limit,
                    observed=outcome.observed,
                    had_error=bool(error_message) or outcome.bot_blocked,
                    search_blocked=search_blocked or outcome.bot_blocked,
                )
            universe = dict(outcome.universe or {})
            universe["requested"] = limit
            universe["observed"] = outcome.observed
            universe["completeness"] = completeness
            universe.setdefault("extracted", len(outcome.success) + len(outcome.skipped_irrelevant) + len(outcome.unknown))
            universe.setdefault("valid", len(outcome.success))
            universe.setdefault("excluded", len(outcome.skipped_irrelevant))
            universe.setdefault("unknown", len(outcome.unknown))
            universe.setdefault("failed", len(outcome.failed))
            universe.setdefault("duplicate", len(outcome.skipped_duplicates))
            universe.setdefault("inaccessible", 0)
            universe.update({k: stats[k] for k in stats if k not in universe or universe.get(k) is None})
            for key in (
                "pages_attempted",
                "pages_inspected",
                "pages_blocked",
                "pagination_reliable",
                "last_observed_position",
                "search_status",
                "query",
                "queries",
                "stop_reason",
                "strata",
                "universe_slots",
            ):
                if key in stats:
                    universe[key] = stats[key]
            universe["ranking_scope"] = "stratum_query"
            universe["universe_slot_is_retailer_rank"] = False
            universe["reconciles"] = outcome.observed == (
                int(universe.get("valid") or 0)
                + int(universe.get("excluded") or 0)
                + int(universe.get("unknown") or 0)
                + int(universe.get("failed") or 0)
                + int(universe.get("duplicate") or 0)
                + int(universe.get("inaccessible") or 0)
            )
            outcome.universe = universe
            outcome.discovery_stats = stats
            status = run_status_from_completeness(str(completeness))
            items_collected = outcome.observed
            try:
                self._persist_stratified_search_observations(run_id, outcome)
                self.session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "stratified_search_observations_persist_failed",
                    extra={
                        "event": "stratified_search_observations_persist_failed",
                        "retailer": self.collector.code,
                        "run_id": run_id,
                        "error": str(exc),
                    },
                )
                self.session.rollback()
                persist_error = f"search_observations_persist_failed: {exc}"
                error_message = (
                    f"{error_message}; {persist_error}"
                    if error_message
                    else persist_error
                )
        else:
            status = "completed"
            if error_message and not outcome.success:
                status = "failed"
            elif outcome.failed or error_message:
                status = "partial"
            items_collected = len(outcome.success)

        # Re-attach run after possible rollbacks.
        from database.models import CollectionRun

        run_row = self.session.get(CollectionRun, run_id)
        if run_row is not None:
            if observed_universe and outcome.universe:
                meta = dict(run_row.run_metadata or {})
                meta["universe"] = outcome.universe
                run_row.run_metadata = meta
            self.persister.complete_run(
                run_row,
                status=status,
                items_collected=items_collected,
                error_message=error_message,
            )
            self.session.commit()

        outcome.collection_run_id = run_id
        outcome.status = status
        logger.info(
            "collection_finished",
            extra={
                "event": "collection_finished",
                "retailer": self.collector.code,
                "run_id": run_id,
                "count": len(outcome.success),
                "observed": outcome.observed,
            },
        )
        return outcome

    def _persist_stratified_search_observations(
        self, run_id: int, outcome: CollectionOutcome
    ) -> int:
        """Write one search_observations row per observed stratified SERP slot."""
        from collector.search.persist import persist_stratified_catalog_observations

        universe = outcome.universe or {}
        slots = list(universe.get("observations") or [])
        if not slots:
            logger.warning(
                "stratified_search_observations_empty",
                extra={
                    "event": "stratified_search_observations_empty",
                    "retailer": self.collector.code,
                    "run_id": run_id,
                    "observed": outcome.observed,
                },
            )
            return 0
        written = persist_stratified_catalog_observations(
            self.session,
            collection_run_id=run_id,
            retailer_code=self.collector.code,
            country_code=self.collector.country_code,
            slots=slots,
            strata_reports=list(universe.get("strata") or []),
        )
        logger.info(
            "stratified_search_observations_persisted",
            extra={
                "event": "stratified_search_observations_persisted",
                "retailer": self.collector.code,
                "run_id": run_id,
                "count": written,
                "observed": outcome.observed,
                "slots": len(slots),
            },
        )
        if written != len(slots):
            logger.error(
                "stratified_search_observations_count_mismatch",
                extra={
                    "event": "stratified_search_observations_count_mismatch",
                    "retailer": self.collector.code,
                    "run_id": run_id,
                    "written": written,
                    "slots": len(slots),
                },
            )
        return written

    async def _collect_observed_universe(
        self,
        browser,
        candidates,
        *,
        outcome: CollectionOutcome,
        limit: int,
        run_id: int,
        seen_skus: set[str],
    ) -> None:
        """Observe SERP slots then classify. Excluded items keep their position."""
        from collections import Counter

        from collector.observation import (
            STATUS_DUPLICATE,
            STATUS_EXCLUDED,
            STATUS_FAILED,
            STATUS_INACCESSIBLE,
            STATUS_UNKNOWN,
            STATUS_VALID,
            ObservationCounters,
            attach_observation_classification,
            observation_bucket,
            overall_completeness_from_strata,
        )

        counters = ObservationCounters(requested=limit)
        slot_records: list[dict] = []
        brand_counts: Counter[str] = Counter()
        oem_counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        brand_counts_observations: Counter[str] = Counter()
        seen_skus_by_query: dict[str, set[str]] = {}
        counted_identity: set[str] = set()

        def _inaccessible_error(message: str) -> bool:
            lowered = message.lower()
            return any(
                token in lowered
                for token in (
                    "bot challenge",
                    "account_verification",
                    "account verification",
                    "unusual traffic",
                    "blocked",
                )
            )

        def _stamp(product, candidate, bucket: str, observed_at, *, position: int) -> None:
            attach_observation_classification(product)
            raw = dict(product.raw_payload or {})
            cand_raw = candidate.raw if isinstance(candidate.raw, dict) else {}
            clf = raw.get("classification") if isinstance(raw.get("classification"), dict) else {}
            stratum = candidate.stratum or cand_raw.get("stratum")
            universe_slot = candidate.universe_slot or cand_raw.get("universe_slot")
            raw["observation"] = {
                "search_position": candidate.search_position or position,
                "search_page": candidate.search_page,
                "query": candidate.query,
                "stratum": stratum,
                "universe_slot": universe_slot,
                "ranking_scope": "stratum_query",
                "universe_slot_is_retailer_rank": False,
                "retailer": self.collector.code,
                "country": self.collector.country_code,
                "url": candidate.source_url,
                "sku": product.retailer_sku,
                "raw_title": candidate.title or product.title,
                "normalized_title": product.title,
                "observed_at": observed_at.isoformat(),
                "extraction_status": "EXTRACTED",
                "extraction_source": "listing+pdp",
                "bucket": bucket,
                "eligibility": bucket,
                "is_sponsored": bool(cand_raw.get("is_sponsored")),
                "repeat_promotion": bool(cand_raw.get("repeat_promotion")),
                "used_fallback": bool(cand_raw.get("used_fallback")),
                "discovery_surface": cand_raw.get("discovery_surface"),
                "brand_evidence": clf.get("brand_reason"),
                "oem_evidence": clf.get("oem_reason"),
                "product_type_evidence": (clf or {}).get("reasons"),
                "gaming": bool((clf or {}).get("gaming")),
                "exclusion_reason": (clf or {}).get("exclusion_reason"),
            }
            product.raw_payload = raw
            identity_key = f"{self.collector.code}|{self.collector.country_code}|{product.retailer_sku}"
            brand_counts_observations[str(product.brand or "UNKNOWN")] += 1
            if identity_key not in counted_identity:
                counted_identity.add(identity_key)
                brand_counts[str(product.brand or "UNKNOWN")] += 1
                oem_counts[str(product.oem or "UNKNOWN")] += 1
                type_counts[str(product.product_type or "UNKNOWN")] += 1

        for candidate in candidates:
            if counters.observed >= limit:
                break
            position = candidate.search_position or (counters.observed + 1)
            page_number = candidate.search_page
            query = candidate.query or ""
            sku = (candidate.retailer_sku or "").strip()
            cand_raw = candidate.raw if isinstance(candidate.raw, dict) else {}
            stratum = candidate.stratum or cand_raw.get("stratum")
            universe_slot = candidate.universe_slot or cand_raw.get("universe_slot") or (
                counters.observed + 1
            )
            slot = {
                "search_position": position,
                "search_page": page_number,
                "query": query,
                "stratum": stratum,
                "universe_slot": universe_slot,
                "ranking_scope": "stratum_query",
                "retailer": self.collector.code,
                "country": self.collector.country_code,
                "url": candidate.source_url,
                "sku": sku or None,
                "title": candidate.title,
                "is_sponsored": bool(cand_raw.get("is_sponsored")),
                "used_fallback": bool(cand_raw.get("used_fallback")),
            }
            if not sku:
                counters.record(STATUS_FAILED)
                slot["extraction_status"] = "FAILED"
                slot["bucket"] = STATUS_FAILED
                slot_records.append(slot)
                outcome.failed.append(
                    {"url": candidate.source_url, "error": "missing_sku", **slot}
                )
                continue
            query_seen = seen_skus_by_query.setdefault(query, set())
            if sku in query_seen:
                counters.record(STATUS_DUPLICATE)
                slot["extraction_status"] = "DUPLICATE"
                slot["bucket"] = STATUS_DUPLICATE
                slot["repeat_promotion"] = bool(cand_raw.get("is_sponsored")) or (
                    candidate.search_page or 1
                ) > 1
                slot_records.append(slot)
                outcome.skipped_duplicates.append(sku)
                continue
            query_seen.add(sku)
            seen_skus.add(sku)

            try:
                product = await self.collector.fetch_product(browser, candidate)
                attach_observation_classification(product)
                bucket = observation_bucket(product)
                observed_at = datetime.now(timezone.utc)
                _stamp(product, candidate, bucket, observed_at, position=position)
                product_id = self.persister.save_product(
                    product,
                    collection_run_id=run_id,
                    observed_at=observed_at,
                )
                self._persist_surface_evidence(
                    product,
                    product_id=product_id,
                    collection_run_id=run_id,
                    observed_at=observed_at,
                )
                self.session.commit()
                counters.record(bucket, product)
                slot["extraction_status"] = "EXTRACTED"
                slot["bucket"] = bucket
                slot["brand"] = product.brand
                slot["oem"] = product.oem
                slot["product_type"] = product.product_type
                slot["exclusion_status"] = bucket
                clf = (product.raw_payload or {}).get("classification") or {}
                if isinstance(clf, dict):
                    slot["exclusion_reason"] = clf.get("exclusion_reason")
                    slot["product_type"] = clf.get("product_type") or product.product_type
                    slot["gaming"] = bool(clf.get("gaming"))
                slot["product_id"] = product_id
                slot_records.append(slot)
                if bucket == STATUS_VALID:
                    outcome.success.append(product)
                elif bucket == STATUS_EXCLUDED:
                    outcome.skipped_irrelevant.append(
                        {
                            "sku": sku,
                            "url": candidate.source_url,
                            "title": product.title,
                            "product_type": product.product_type,
                            "reason": "excluded_observed",
                            "search_position": position,
                        }
                    )
                elif bucket == STATUS_UNKNOWN:
                    outcome.unknown.append(
                        {
                            "sku": sku,
                            "url": candidate.source_url,
                            "title": product.title,
                            "product_type": product.product_type,
                            "reason": "unknown_observed",
                            "search_position": position,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - per-product isolation
                self.session.rollback()
                logger.exception(
                    "product_failed",
                    extra={
                        "event": "product_failed",
                        "retailer": self.collector.code,
                        "run_id": run_id,
                        "sku": sku,
                        "url": candidate.source_url,
                        "error": str(exc),
                    },
                )
                inaccessible = _inaccessible_error(str(exc))
                bucket = STATUS_INACCESSIBLE if inaccessible else STATUS_FAILED
                counters.record(bucket)
                slot["extraction_status"] = bucket
                slot["bucket"] = bucket
                slot["error"] = str(exc)
                slot_records.append(slot)
                outcome.failed.append(
                    {
                        "sku": sku,
                        "url": candidate.source_url,
                        "error": str(exc),
                        "search_position": position,
                        "bucket": bucket,
                    }
                )

        stats = dict(getattr(self.collector, "discovery_stats", None) or {})
        search_blocked = str(stats.get("search_status") or "") == "BLOCKED"
        strata = list(stats.get("strata") or [])
        if strata:
            completeness = overall_completeness_from_strata(
                strata,
                requested=limit,
                observed=counters.observed,
                search_blocked=search_blocked,
            )
        else:
            completeness = counters.completeness(
                had_error=False, search_blocked=search_blocked
            )
        outcome.observed = counters.observed
        outcome.universe = counters.as_dict(
            completeness=completeness,
            pages_attempted=stats.get("pages_attempted", 0),
            pages_inspected=stats.get("pages_inspected", 0),
            pages_blocked=stats.get("pages_blocked", 0),
            pagination_reliable=stats.get("pagination_reliable", True),
            last_observed_position=stats.get("last_observed_position", counters.observed),
            search_status=stats.get("search_status", "OK"),
            query=stats.get("query"),
            queries=stats.get("queries"),
            stop_reason=stats.get("stop_reason"),
            strata=strata,
            ranking_scope="stratum_query",
            universe_slot_is_retailer_rank=False,
            brand_counts=dict(brand_counts),
            oem_counts=dict(oem_counts),
            product_type_counts=dict(type_counts),
            brand_counts_observations=dict(brand_counts_observations),
            unique_identities=len(counted_identity),
            observations=slot_records,
            inaccessible_scope="candidate",
        )
        logger.info(
            "collection_universe",
            extra={
                "event": "collection_universe",
                "retailer": self.collector.code,
                "run_id": run_id,
                **{k: v for k, v in outcome.universe.items() if k != "observations"},
            },
        )

    def _browser_session(self) -> BrowserSession:
        factory = getattr(self.collector, "build_browser_session", None)
        if callable(factory):
            return factory()
        return BrowserSession()

    def _persist_surface_evidence(
        self,
        product,
        *,
        product_id: int,
        collection_run_id: int,
        observed_at,
    ) -> None:
        """Append badge/audit rows from captured listing/PDP evidence.

        Mercado Libre and Newegg share ``CollectionPersister.save_audits`` /
        ``save_badges`` and the S1–P5 engine. EXCLUDED / ``other`` products are
        not written as normal compliance observations.
        """
        if product.retailer_code not in {"mercadolibre", "newegg"}:
            return
        if not _compliance_persist_eligible(product):
            return
        raw = product.raw_payload or {}
        listing = raw.get("listing_audit") if isinstance(raw.get("listing_audit"), dict) else {}
        pdp = raw.get("pdp_audit") if isinstance(raw.get("pdp_audit"), dict) else {}
        signals = raw.get("badge_signals") if isinstance(raw.get("badge_signals"), dict) else {}

        from collector.audit.models import ListingEvidence
        from collector.parsers.badges import BadgeEvidence

        listing_ev = ListingEvidence(
            title=listing.get("title") or product.title,
            tile_text=listing.get("tile_text"),
            badge_texts=list(listing.get("badge_texts") or []),
            selectors_used=list(listing.get("selectors_used") or ["listing_card"]),
            source_url=listing.get("source_url") or product.source_url,
            available=bool(listing.get("available", True)),
        )
        badge_texts = list(signals.get("badge_texts") or []) + list(
            pdp.get("badge_texts") or []
        )
        img_alts = list(signals.get("img_alts") or [])
        self.persister.save_badges(
            product,
            product_id=product_id,
            collection_run_id=collection_run_id,
            evidence=BadgeEvidence(
                badge_texts=badge_texts,
                img_alts=img_alts,
                img_titles=list(signals.get("img_titles") or []),
                element_titles=list(signals.get("element_titles") or [])
                + list(signals.get("aria_labels") or []),
                element_texts=list(signals.get("badge_texts") or []),
                page_text=None,
                source_url=product.source_url,
            ),
            observed_at=observed_at,
        )
        self.persister.save_audits(
            product,
            product_id=product_id,
            collection_run_id=collection_run_id,
            listing=listing_ev,
            badge_texts=badge_texts,
            brand_media_signals=list(pdp.get("brand_media_signals") or img_alts),
            oem_media_signals=list(pdp.get("oem_media_signals") or img_alts),
            page_text=None,
            badges_inspected=bool(pdp.get("badges_inspected")),
            media_inspected=bool(pdp.get("media_inspected")),
            selectors_used=list(pdp.get("selectors_used") or []),
            observed_at=observed_at,
        )

