"""Badge collection for existing Mercado Libre products.

Reuses the common badge detector/evaluator. When the PDP is blocked by
account-verification, falls back to title/spec text already stored on the
product (never invents badge presence).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import text

from collector.browser import BrowserSession
from collector.logging_utils import setup_logging
from collector.normalize import NormalizedProduct
from collector.parsers.badges import BadgeEvidence
from collector.persist import CollectionPersister
from collector.retailers.mercadolibre.product_page import (
    is_account_verification,
    is_bot_challenge,
    specs_from_title,
)
from database.connection import get_engine, get_session_factory
from database.repositories import CollectionRunRepository

logger = logging.getLogger("collector.badges.run_mercadolibre")

MAX_PRODUCTS = 20


def load_existing_products(session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
                p.id, p.retailer_code, p.country_code, p.retailer_sku,
                p.canonical_url, p.title, p.brand, p.oem, p.product_type,
                p.category_raw,
                (
                    SELECT s.raw_payload FROM product_snapshots s
                    WHERE s.product_id = p.id
                    ORDER BY s.observed_at DESC LIMIT 1
                ) AS raw_payload
            FROM products p
            WHERE p.retailer_code = 'mercadolibre'
              AND p.country_code = 'BR'
            ORDER BY p.id
            LIMIT :lim
            """
        ),
        {"lim": MAX_PRODUCTS},
    ).mappings().all()
    return [dict(r) for r in rows]


def product_to_normalized(row: dict[str, Any]) -> NormalizedProduct:
    raw = row.get("raw_payload") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    specs = specs_from_title(row.get("title"))
    processor = raw.get("processor") or specs.get("Processador")
    return NormalizedProduct(
        retailer_code=row["retailer_code"],
        country_code=row["country_code"],
        retailer_sku=row["retailer_sku"],
        source_url=row["canonical_url"],
        title=row.get("title"),
        brand=row.get("brand"),
        oem=row.get("oem"),
        product_type=row.get("product_type"),
        category_raw=row.get("category_raw"),
        processor=processor,
        raw_payload={**raw, **specs},
    )


def evidence_from_title(product: dict[str, Any]) -> BadgeEvidence:
    title = product.get("title") or ""
    specs = specs_from_title(title)
    texts = [title, *specs.values()]
    return BadgeEvidence(
        badge_texts=texts,
        img_alts=[],
        img_titles=[],
        element_titles=[],
        element_texts=texts,
        page_text=title,
        source_url=product.get("canonical_url"),
    )


async def process_product_page(
    browser: BrowserSession, *, sku: str, url: str, fallback: BadgeEvidence
) -> tuple[BadgeEvidence, Optional[str]]:
    page = await browser.new_page()
    try:
        await browser.goto(page, url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        html = await page.content()
        title = await page.title()
        if is_account_verification(html, page.url) or is_account_verification(title, page.url):
            return fallback, None  # not a hard failure — use listing evidence
        if is_bot_challenge(html) or is_bot_challenge(title):
            return fallback, None
        shot = await browser.screenshot(page, label=f"ml_badge_{sku}")
        body = ""
        try:
            body = (await page.inner_text("body"))[:5000]
        except Exception:  # noqa: BLE001
            pass
        alts = []
        imgs = page.locator("img[alt]")
        for i in range(min(await imgs.count(), 30)):
            alt = await imgs.nth(i).get_attribute("alt")
            if alt and alt.strip():
                alts.append(alt.strip())
        return (
            BadgeEvidence(
                badge_texts=[body[:500]] if body else [],
                img_alts=alts,
                img_titles=[],
                element_titles=[],
                element_texts=[],
                page_text=body or fallback.page_text,
                screenshot_path=shot,
                source_url=url.split("?")[0],
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return fallback, None
    finally:
        await page.close()


async def run_badge_collection(*, collection_run_id: int | None = None) -> dict[str, Any]:
    setup_logging()
    load_dotenv()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    db = session_factory()

    summary: dict[str, Any] = {
        "original_products": 0,
        "processed": 0,
        "failed": 0,
        "badge_rows_inserted": 0,
        "products_no_badge_evidence": 0,
        "failed_products": [],
        "processed_product_ids": [],
        "collection_run_id": None,
        "errors": [],
        "retailer": "mercadolibre",
    }

    try:
        products = load_existing_products(db)
        summary["original_products"] = len(products)
        if not products:
            summary["errors"].append("no_mercadolibre_products_found")
            return summary

        if collection_run_id is not None:
            run_id = collection_run_id
        else:
            run = CollectionRunRepository(db).start(
                retailer_code="mercadolibre",
                country_code="BR",
                run_type="badges",
                run_metadata={
                    "source": "collector.badges.run_mercadolibre_existing",
                    "product_count": len(products),
                },
            )
            db.commit()
            run_id = run.id
        summary["collection_run_id"] = run_id
        observed_at = datetime.now(timezone.utc)
        persister = CollectionPersister(db)

        async with BrowserSession() as browser:
            for product in products:
                sku = product["retailer_sku"]
                url = product["canonical_url"]
                product_id = int(product["id"])
                fallback = evidence_from_title(product)
                evidence, err = await process_product_page(
                    browser, sku=sku, url=url, fallback=fallback
                )
                if err:
                    summary["failed"] += 1
                    summary["failed_products"].append(
                        {"product_id": product_id, "sku": sku, "reason": err}
                    )
                    continue
                normalized = product_to_normalized(product)
                try:
                    before = db.execute(
                        text("SELECT COUNT(*) FROM badges WHERE collection_run_id = :rid"),
                        {"rid": run_id},
                    ).scalar()
                    persister.save_badges(
                        normalized,
                        product_id=product_id,
                        collection_run_id=run_id,
                        evidence=evidence,
                        include_promotional=True,
                        observed_at=observed_at,
                    )
                    db.commit()
                    after = db.execute(
                        text("SELECT COUNT(*) FROM badges WHERE collection_run_id = :rid"),
                        {"rid": run_id},
                    ).scalar()
                    inserted = int(after or 0) - int(before or 0)
                    summary["badge_rows_inserted"] += inserted
                    summary["processed"] += 1
                    summary["processed_product_ids"].append(product_id)
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    summary["failed"] += 1
                    summary["failed_products"].append(
                        {
                            "product_id": product_id,
                            "sku": sku,
                            "reason": f"persist_error:{exc}",
                        }
                    )
                await asyncio.sleep(0.4)

        from database.models import CollectionRun

        run_row = db.get(CollectionRun, run_id)
        if run_row is not None:
            CollectionRunRepository(db).complete(
                run_row,
                status="completed" if summary["failed"] == 0 else "partial",
                items_collected=summary["processed"],
                error_message="; ".join(
                    f["sku"] for f in summary["failed_products"][:5]
                )
                or None,
            )
            db.commit()
    finally:
        db.close()
        engine.dispose()

    return summary


def main() -> None:
    print(json.dumps(asyncio.run(run_badge_collection()), indent=2))


if __name__ == "__main__":
    main()
