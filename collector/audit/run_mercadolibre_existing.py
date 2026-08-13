"""Run S1–P5 audits against existing Mercado Libre products.

Reuses the common audit engine. Product-detail pages are often gated behind
Mercado Libre account-verification in automated CDP sessions; when blocked we
keep listing/title evidence for S1/P1 only and leave P2–P5 UNKNOWN when the
required product-page evidence was not inspected (never invent FAIL).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import text

from collector.audit.engine import evaluate_and_persist
from collector.audit.models import AuditContext, ListingEvidence, ProductEvidence
from collector.browser import BrowserSession
from collector.logging_utils import setup_logging
from collector.retailers.mercadolibre.product_page import (
    is_account_verification,
    is_bot_challenge,
    specs_from_title,
)
from collector.retailers.mercadolibre.selectors import (
    PRODUCT_TITLE_SELECTORS,
    SPEC_ROW_SELECTORS,
)
from database.connection import get_engine, get_session_factory
from database.repositories import CollectionRunRepository

logger = logging.getLogger("collector.audit.run_mercadolibre")

MAX_PRODUCTS = 20
OFERTAS_URL = "https://www.mercadolivre.com.br/ofertas?category=MLB1652"


def load_products(session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT id, retailer_sku, title, brand, oem, product_type, canonical_url,
                   retailer_code, country_code
            FROM products
            WHERE retailer_code = 'mercadolibre'
              AND country_code = 'BR'
            ORDER BY id
            LIMIT :lim
            """
        ),
        {"lim": MAX_PRODUCTS},
    ).mappings().all()
    return [dict(r) for r in rows]


def evidence_from_stored_product(product: dict[str, Any]) -> tuple[ListingEvidence, ProductEvidence]:
    """Build listing/PDP evidence from persisted fields when live PDP is blocked.

    Listing title may support S1/P1. It must NOT be promoted into PDP badge
    (``page_text``) or specs-table evidence — missing PDP inspection → UNKNOWN
    for P2/P3/P4/P5.
    """
    title = product.get("title")
    listing = ListingEvidence(
        title=title,
        tile_text=None,
        badge_texts=[],
        selectors_used=["products.title"],
        source_url=OFERTAS_URL,
        available=bool(title),
    )
    product_ev = ProductEvidence(
        title=title,
        specs={},
        specs_available=False,
        page_text=None,
        badge_texts=[],
        brand_media_signals=[],
        oem_media_signals=[],
        media_inspected=False,
        badges_inspected=False,
        selectors_used=["products.title"],
        source_url=product.get("canonical_url"),
        available=bool(title),
        access_reason="PDP_BLOCKED",
    )
    return listing, product_ev


async def capture_product_evidence(
    browser: BrowserSession, *, sku: str, product_url: str, fallback: ProductEvidence
) -> ProductEvidence:
    page = await browser.new_page()
    try:
        await browser.goto(page, product_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html = await page.content()
        title = await page.title()
        if is_account_verification(html, page.url) or is_account_verification(title, page.url):
            logger.warning(
                "ml_audit_pdp_verification",
                extra={"event": "ml_audit_pdp_verification", "sku": sku},
            )
            # Keep listing-title for P1; do not invent PDP badge/specs evidence.
            return fallback
        if is_bot_challenge(html) or is_bot_challenge(title):
            return fallback

        h1 = None
        for sel in PRODUCT_TITLE_SELECTORS:
            loc = page.locator(sel)
            if await loc.count():
                h1 = (await loc.first.inner_text()).strip()
                break
        specs: dict[str, str] = {}
        for sel in SPEC_ROW_SELECTORS:
            rows = page.locator(sel)
            count = await rows.count()
            for i in range(min(count, 40)):
                cells = rows.nth(i).locator("th, td")
                if await cells.count() >= 2:
                    k = (await cells.nth(0).inner_text()).strip()
                    v = (await cells.nth(1).inner_text()).strip()
                    if k and v:
                        specs[k] = v
            if specs:
                break
        # Title heuristics may enrich display fields but do not mark specs_available
        # unless a real specification table was present on the PDP.
        specs_available = bool(specs)
        for k, v in specs_from_title(h1 or fallback.title).items():
            specs.setdefault(k, v)
        shot = await browser.screenshot(page, label=f"ml_audit_{sku}")
        return ProductEvidence(
            title=h1 or fallback.title,
            specs=specs,
            specs_available=specs_available,
            page_text=((await page.inner_text("body"))[:4000] if True else None),
            badge_texts=[],
            brand_media_signals=[],
            oem_media_signals=[],
            media_inspected=True,
            badges_inspected=True,
            selectors_used=PRODUCT_TITLE_SELECTORS[:2] + SPEC_ROW_SELECTORS[:2],
            source_url=product_url.split("?")[0],
            screenshot_path=shot,
            available=True,
            access_reason="SPECS_AVAILABLE" if specs_available else "SPECS_NOT_FOUND",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ml_audit_pdp_failed",
            extra={"event": "ml_audit_pdp_failed", "sku": sku, "error": str(exc)},
        )
        return fallback
    finally:
        await page.close()


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
        "retailer": "mercadolibre",
    }

    try:
        products = load_products(db)
        if not products:
            summary["errors"].append("no_mercadolibre_products_found")
            return summary

        run = CollectionRunRepository(db).start(
            retailer_code="mercadolibre",
            country_code="BR",
            run_type="audit",
            run_metadata={
                "source": "collector.audit.run_mercadolibre_existing",
                "product_count": len(products),
            },
        )
        db.commit()
        run_id = run.id
        observed_at = datetime.now(timezone.utc)

        async with BrowserSession() as browser:
            for product in products:
                sku = product["retailer_sku"]
                url = product["canonical_url"]
                listing, fallback_product = evidence_from_stored_product(product)
                product_ev = await capture_product_evidence(
                    browser, sku=sku, product_url=url, fallback=fallback_product
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
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    summary["errors"].append(f"{sku}:{exc}")
                await asyncio.sleep(0.4)

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
    print(json.dumps(asyncio.run(run_audit()), indent=2))


if __name__ == "__main__":
    main()
