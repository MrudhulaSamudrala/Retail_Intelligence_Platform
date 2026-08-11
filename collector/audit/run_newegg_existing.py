"""Run S1–P5 audits against existing Newegg products in PostgreSQL.

Uses Playwright (preferably CDP) to inspect live listing/product pages.
Does not collect new products or change the audit engine logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import text

from collector.audit.engine import evaluate_and_persist
from collector.audit.models import AuditContext, ListingEvidence, ProductEvidence
from collector.browser import BrowserSession
from collector.logging_utils import setup_logging
from collector.retailers.newegg.product_page import extract_specs, is_bot_challenge
from collector.retailers.newegg.selectors import (
    LISTING_ITEM_SELECTORS,
    PRODUCT_TITLE_SELECTORS,
    SPEC_ROW_SELECTORS,
)
from database.connection import get_engine, get_session_factory
from database.repositories import CollectionRunRepository

logger = logging.getLogger("collector.audit.run_newegg")

LISTING_URL = "https://www.newegg.com/p/pl?d=gaming+laptop"
MAX_PRODUCTS = 20


def _sku_variants(sku: str, url: str) -> set[str]:
    variants = {sku.upper(), sku}
    path = urlparse(url).path or ""
    m = re.search(r"/p/([A-Za-z0-9\-]+)", path, re.I)
    if m:
        variants.add(m.group(1).upper())
        variants.add(m.group(1))
    return {v for v in variants if v}


async def _first_text(page, selectors: list[str]) -> Optional[str]:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if await loc.count() == 0:
                continue
            text = (await loc.first.inner_text(timeout=2000)).strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            continue
    return None


async def _wait_challenge_clear(page, *, attempts: int = 10) -> bool:
    for _ in range(attempts):
        title = await page.title()
        html = await page.content()
        if not (is_bot_challenge(title) or is_bot_challenge(html)):
            return True
        await page.wait_for_timeout(2500)
    return False


async def _collect_badge_texts(root) -> list[str]:
    texts: list[str] = []
    selectors = [
        "img[alt]",
        "[class*='badge']",
        "[class*='brand']",
        ".item-brand",
        ".product-brand",
        "[class*='logo']",
    ]
    for sel in selectors:
        loc = root.locator(sel)
        try:
            count = min(await loc.count(), 20)
        except Exception:  # noqa: BLE001
            continue
        for i in range(count):
            el = loc.nth(i)
            try:
                alt = await el.get_attribute("alt")
                if alt and alt.strip():
                    texts.append(alt.strip())
                title = await el.get_attribute("title")
                if title and title.strip():
                    texts.append(title.strip())
                txt = (await el.inner_text(timeout=500)).strip()
                if txt and len(txt) < 120:
                    texts.append(txt)
            except Exception:  # noqa: BLE001
                continue
    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


async def _collect_media_signals(page) -> tuple[list[str], list[str]]:
    brand_signals: list[str] = []
    oem_signals: list[str] = []
    # Shared pool; matching is brand/OEM-specific in the audit engine.
    imgs = page.locator("img[alt], img[src], video, [class*='gallery'] img")
    try:
        count = min(await imgs.count(), 60)
    except Exception:  # noqa: BLE001
        count = 0
    for i in range(count):
        el = imgs.nth(i)
        try:
            alt = (await el.get_attribute("alt")) or ""
            src = (await el.get_attribute("src")) or ""
            aria = (await el.get_attribute("aria-label")) or ""
            blob = " ".join(x for x in (alt, src, aria) if x).strip()
            if blob:
                brand_signals.append(blob[:300])
                oem_signals.append(blob[:300])
        except Exception:  # noqa: BLE001
            continue
    return brand_signals, oem_signals


async def _find_listing_tile(page, sku: str, product_url: str):
    variants = _sku_variants(sku, product_url)
    for item_sel in LISTING_ITEM_SELECTORS:
        cards = page.locator(item_sel)
        try:
            count = await cards.count()
        except Exception:  # noqa: BLE001
            continue
        if count == 0:
            continue
        for i in range(count):
            card = cards.nth(i)
            try:
                html = await card.inner_html()
            except Exception:  # noqa: BLE001
                continue
            upper = html.upper()
            if any(v.upper() in upper for v in variants):
                return card, item_sel
    return None, None


async def capture_listing_evidence(
    session: BrowserSession,
    page,
    *,
    sku: str,
    product_url: str,
) -> ListingEvidence:
    shot = await session.screenshot(page, label=f"audit_listing_{sku}")
    if is_bot_challenge(await page.title()) or is_bot_challenge(await page.content()):
        return ListingEvidence(
            available=False,
            source_url=LISTING_URL,
            screenshot_path=shot,
            selectors_used=[],
        )

    card, item_sel = await _find_listing_tile(page, sku, product_url)
    if card is None:
        return ListingEvidence(
            available=True,
            title=None,
            tile_text=None,
            badge_texts=[],
            selectors_used=LISTING_ITEM_SELECTORS,
            source_url=LISTING_URL,
            screenshot_path=shot,
        )

    title = None
    for sel in ["a.item-title", ".item-title", "a[href*='/p/']"]:
        loc = card.locator(sel)
        try:
            if await loc.count():
                title = (await loc.first.inner_text(timeout=1500)).strip() or title
                if title:
                    break
        except Exception:  # noqa: BLE001
            continue

    try:
        tile_text = (await card.inner_text()).strip()
    except Exception:  # noqa: BLE001
        tile_text = None

    badges = await _collect_badge_texts(card)
    return ListingEvidence(
        title=title,
        tile_text=tile_text,
        badge_texts=badges,
        selectors_used=[item_sel or ".item-cell", "a.item-title", "[class*='badge']", "img[alt]"],
        source_url=LISTING_URL,
        screenshot_path=shot,
        available=True,
    )


async def capture_product_evidence(
    session: BrowserSession,
    *,
    sku: str,
    product_url: str,
) -> ProductEvidence:
    page = await session.new_page()
    try:
        await session.goto(page, product_url)
        cleared = await _wait_challenge_clear(page)
        shot = await session.screenshot(page, label=f"audit_product_{sku}")
        if not cleared or is_bot_challenge(await page.title()) or is_bot_challenge(await page.content()):
            return ProductEvidence(
                available=False,
                source_url=product_url,
                screenshot_path=shot,
                selectors_used=[],
            )

        title = await _first_text(page, PRODUCT_TITLE_SELECTORS)
        specs = await extract_specs(page)
        badges = await _collect_badge_texts(page)
        brand_media, oem_media = await _collect_media_signals(page)
        try:
            body_text = (await page.locator("body").inner_text(timeout=5000))[:8000]
        except Exception:  # noqa: BLE001
            body_text = None

        return ProductEvidence(
            title=title,
            specs=specs,
            specs_available=True,  # inspected; may be empty → FAIL not UNKNOWN for P3
            page_text=body_text,
            badge_texts=badges,
            brand_media_signals=brand_media,
            oem_media_signals=oem_media,
            media_inspected=True,
            badges_inspected=True,
            selectors_used=PRODUCT_TITLE_SELECTORS[:2] + SPEC_ROW_SELECTORS[:2] + ["img[alt]"],
            source_url=product_url.split("?")[0],
            screenshot_path=shot,
            available=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("product_evidence_failed", extra={"sku": sku, "error": str(exc)})
        return ProductEvidence(
            available=False,
            source_url=product_url,
            selectors_used=[],
        )
    finally:
        await page.close()


def load_products(session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT id, retailer_sku, title, brand, oem, product_type, canonical_url,
                   retailer_code, country_code
            FROM products
            WHERE retailer_code = 'newegg'
              AND country_code = 'US'
              AND retailer_sku <> 'PL'
            ORDER BY id
            LIMIT :lim
            """
        ),
        {"lim": MAX_PRODUCTS},
    ).mappings().all()
    return [dict(r) for r in rows]


async def run_audit() -> dict[str, Any]:
    setup_logging()
    load_dotenv()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    db = session_factory()

    summary: dict[str, Any] = {
        "products_audited": 0,
        "checks_executed": 0,
        "rows_inserted": 0,
        "by_check": {},
        "examples": [],
        "errors": [],
    }

    try:
        products = load_products(db)
        if not products:
            summary["errors"].append("no_newegg_products_found")
            return summary

        run = CollectionRunRepository(db).start(
            retailer_code="newegg",
            country_code="US",
            run_type="audit",
            run_metadata={
                "source": "collector.audit.run_newegg_existing",
                "product_count": len(products),
                "listing_url": LISTING_URL,
            },
        )
        db.commit()
        run_id = run.id
        observed_at = datetime.now(timezone.utc)

        async with BrowserSession() as browser:
            listing_page = await browser.new_page()
            listing_blocked = False
            try:
                await browser.goto(listing_page, LISTING_URL)
                ok = await _wait_challenge_clear(listing_page)
                if not ok:
                    listing_blocked = True
                    logger.warning(
                        "listing_blocked",
                        extra={"event": "listing_blocked", "url": LISTING_URL},
                    )
                else:
                    await listing_page.mouse.wheel(0, 2800)
                    await listing_page.wait_for_timeout(1200)
            except Exception as exc:  # noqa: BLE001
                listing_blocked = True
                summary["errors"].append(f"listing_navigation_failed:{exc}")

            all_results = []
            for product in products:
                sku = product["retailer_sku"]
                url = product["canonical_url"]
                logger.info(
                    "audit_product_start",
                    extra={"event": "audit_product_start", "sku": sku, "url": url},
                )

                if listing_blocked:
                    listing = ListingEvidence(
                        available=False,
                        source_url=LISTING_URL,
                        selectors_used=[],
                    )
                else:
                    listing = await capture_listing_evidence(
                        browser, listing_page, sku=sku, product_url=url
                    )

                product_ev = await capture_product_evidence(
                    browser, sku=sku, product_url=url
                )

                ctx = AuditContext(
                    retailer_code=product["retailer_code"],
                    country_code=product["country_code"],
                    brand=product["brand"],
                    oem=product["oem"],
                    product_type=product["product_type"],
                    product_id=product["id"],
                    collection_run_id=run_id,
                    observed_at=observed_at,
                    listing=listing,
                    product=product_ev,
                )
                try:
                    results = evaluate_and_persist(db, ctx)
                    db.commit()
                    summary["products_audited"] += 1
                    summary["checks_executed"] += len(results)
                    summary["rows_inserted"] += len(results)
                    all_results.extend(results)
                    if len(summary["examples"]) < 5:
                        summary["examples"].append(
                            {
                                "sku": sku,
                                "brand": product["brand"],
                                "oem": product["oem"],
                                "checks": [
                                    {
                                        "code": r.check_code,
                                        "result": r.result,
                                        "evidence": (r.evidence_text or "")[:180],
                                        "reason": (r.details or {}).get("reason"),
                                    }
                                    for r in results
                                ],
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    summary["errors"].append(f"{sku}:{exc}")
                    logger.exception("audit_persist_failed", extra={"sku": sku})

                await asyncio.sleep(0.6)

            await listing_page.close()

        # Aggregate counts from DB for this run (source of truth).
        rows = db.execute(
            text(
                """
                SELECT check_code, result, COUNT(*) AS n
                FROM retailer_audits
                WHERE collection_run_id = :run_id
                GROUP BY check_code, result
                ORDER BY check_code, result
                """
            ),
            {"run_id": run_id},
        ).mappings().all()
        by_check: dict[str, dict[str, int]] = {}
        for row in rows:
            by_check.setdefault(row["check_code"], {"PASS": 0, "FAIL": 0, "UNKNOWN": 0})
            by_check[row["check_code"]][row["result"]] = int(row["n"])
        summary["by_check"] = by_check
        summary["collection_run_id"] = run_id
        total = db.execute(
            text("SELECT COUNT(*) FROM retailer_audits WHERE collection_run_id = :run_id"),
            {"run_id": run_id},
        ).scalar()
        summary["rows_inserted"] = int(total or 0)

        from database.models import CollectionRun

        run_row = db.get(CollectionRun, run_id)
        if run_row is not None:
            CollectionRunRepository(db).complete(
                run_row,
                status="completed" if not summary["errors"] else "partial",
                items_collected=summary["products_audited"],
                error_message="; ".join(summary["errors"][:5]) if summary["errors"] else None,
            )
            db.commit()
    finally:
        db.close()
        engine.dispose()

    return summary


def main() -> None:
    summary = asyncio.run(run_audit())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
