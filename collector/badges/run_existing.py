"""Run platform badge collection against existing Newegg products in PostgreSQL.

Loads product URLs / SKUs from the authoritative ``products`` table only.
Does NOT discover or collect new Newegg products.
Does NOT modify product identity rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import text

from collector.browser import BrowserSession
from collector.logging_utils import setup_logging
from collector.normalize import NormalizedProduct
from collector.parsers.badges import BadgeEvidence
from collector.persist import CollectionPersister
from collector.retailers.newegg.product_page import is_bot_challenge
from database.connection import get_engine, get_session_factory
from database.repositories import CollectionRunRepository

logger = logging.getLogger("collector.badges.run_existing")

# Structured evidence selectors (DOM / alt / title / visible text).
# Scoped to primary product chrome — not related-item carousels.
_PRODUCT_ROOT_SELECTORS = [
    "#product-details",
    "#ProductBuy",
    ".product-buy-box",
    ".product-view",
    "#landingpage-price",
    "#landingpage-gallery",
    ".product-main",
    "div.product-wrap",
    "h1.product-title",
]

_BADGE_SELECTORS = [
    "[class*='badge']",
    "[class*='brand']",
    "[class*='logo']",
    ".product-brand",
    "img[alt*='Intel' i]",
    "img[alt*='AMD' i]",
    "img[alt*='Ryzen' i]",
    "img[alt*='Snapdragon' i]",
    "img[alt*='Apple' i]",
    "img[alt*='Core' i]",
    "img[title*='Intel' i]",
    "img[title*='AMD' i]",
    "img[title*='Ryzen' i]",
]


def _looks_like_product_title(text: str) -> bool:
    """Reject related-item / gallery title strings that are not badges."""
    t = text.strip()
    if len(t) > 90:
        return True
    lower = t.lower()
    if lower.startswith("main image of"):
        return True
    title_markers = (
        "geforce",
        "rtx ",
        "gaming laptop",
        "laptop gpu",
        "gb memory",
        "nvme",
        "windows 11",
        "qhd+",
        "fhd+",
        "oled",
    )
    hits = sum(1 for m in title_markers if m in lower)
    return hits >= 2


async def _wait_challenge_clear(page, *, attempts: int = 10) -> bool:
    for _ in range(attempts):
        title = await page.title()
        html = await page.content()
        if not (is_bot_challenge(title) or is_bot_challenge(html)):
            return True
        await page.wait_for_timeout(2500)
    return False


def _dedupe(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        key = t.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t.strip())
    return out


async def _resolve_product_root(page):
    for sel in _PRODUCT_ROOT_SELECTORS:
        loc = page.locator(sel)
        try:
            if await loc.count() > 0:
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return page


async def collect_badge_evidence(page, *, source_url: str, screenshot_path: Optional[str]) -> BadgeEvidence:
    """Collect DOM/text/alt/title badge evidence from the primary product region."""
    badge_texts: list[str] = []
    img_alts: list[str] = []
    img_titles: list[str] = []
    element_titles: list[str] = []
    element_texts: list[str] = []

    root = await _resolve_product_root(page)

    for sel in _BADGE_SELECTORS:
        loc = root.locator(sel)
        try:
            count = min(await loc.count(), 30)
        except Exception:  # noqa: BLE001
            continue
        for i in range(count):
            el = loc.nth(i)
            try:
                alt = await el.get_attribute("alt")
                if alt and alt.strip() and not _looks_like_product_title(alt):
                    img_alts.append(alt.strip())
                    badge_texts.append(alt.strip())
                title = await el.get_attribute("title")
                if title and title.strip() and not _looks_like_product_title(title):
                    if sel.startswith("img") or "img" in sel:
                        img_titles.append(title.strip())
                    else:
                        element_titles.append(title.strip())
                    badge_texts.append(title.strip())
                txt = (await el.inner_text(timeout=400)).strip()
                if txt and len(txt) < 90 and not _looks_like_product_title(txt):
                    element_texts.append(txt)
                    badge_texts.append(txt)
            except Exception:  # noqa: BLE001
                continue

    # Do not feed full body text into badge evaluation: Newegg product pages
    # include related-item / recommendation copy that falsely matches other
    # brand families. Primary evidence is DOM badge regions, alt, and title.
    page_text: Optional[str] = None

    return BadgeEvidence(
        badge_texts=_dedupe(badge_texts),
        img_alts=_dedupe(img_alts),
        img_titles=_dedupe(img_titles),
        element_titles=_dedupe(element_titles),
        element_texts=_dedupe(element_texts),
        page_text=page_text,
        source_url=source_url,
        screenshot_path=screenshot_path,
    )


def load_existing_products(session) -> list[dict[str, Any]]:
    """Authoritative product set: existing Newegg rows only (no discovery)."""
    rows = session.execute(
        text(
            """
            SELECT
                p.id,
                p.retailer_code,
                p.country_code,
                p.retailer_sku,
                p.canonical_url,
                p.title,
                p.brand,
                p.oem,
                p.product_type,
                p.category_raw,
                (
                    SELECT s.raw_payload
                    FROM product_snapshots s
                    WHERE s.product_id = p.id
                    ORDER BY s.observed_at DESC
                    LIMIT 1
                ) AS raw_payload
            FROM products p
            WHERE p.retailer_code = 'newegg'
              AND p.country_code = 'US'
              AND p.retailer_sku <> 'PL'
            ORDER BY p.id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _processor_from_payload(raw_payload: Any) -> Optional[str]:
    if not raw_payload:
        return None
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_payload, dict):
        return None
    for key in ("processor", "cpu", "CPU", "Processor"):
        val = raw_payload.get(key)
        if val:
            return str(val)
    specs = raw_payload.get("specifications") or raw_payload.get("specs") or {}
    if isinstance(specs, dict):
        for key, val in specs.items():
            if val and re.search(r"cpu|processor|chipset", str(key), re.I):
                return str(val)
    return None


def product_to_normalized(row: dict[str, Any]) -> NormalizedProduct:
    raw = row.get("raw_payload") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    processor = _processor_from_payload(raw)
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
        raw_payload=raw,
    )


async def process_product_page(
    browser: BrowserSession,
    *,
    sku: str,
    url: str,
) -> tuple[Optional[BadgeEvidence], Optional[str]]:
    """Open exact product URL and collect badge evidence. Returns (evidence, error)."""
    page = await browser.new_page()
    try:
        await browser.goto(page, url)
        cleared = await _wait_challenge_clear(page)
        shot = await browser.screenshot(page, label=f"badge_product_{sku}")
        if not cleared or is_bot_challenge(await page.title()) or is_bot_challenge(await page.content()):
            return None, "bot_challenge_or_blocked"
        evidence = await collect_badge_evidence(
            page,
            source_url=url.split("?")[0],
            screenshot_path=shot,
        )
        return evidence, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("badge_page_failed", extra={"sku": sku, "error": str(exc)})
        return None, f"page_error:{exc}"
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
    }

    try:
        products = load_existing_products(db)
        summary["original_products"] = len(products)
        if not products:
            summary["errors"].append("no_newegg_products_found")
            return summary

        original_ids = {int(p["id"]) for p in products}
        summary["original_product_ids"] = sorted(original_ids)

        if collection_run_id is not None:
            run_id = collection_run_id
        else:
            run = CollectionRunRepository(db).start(
                retailer_code="newegg",
                country_code="US",
                run_type="badges",
                run_metadata={
                    "source": "collector.badges.run_existing",
                    "product_count": len(products),
                    "product_ids": sorted(original_ids),
                    "discovery": False,
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
                logger.info(
                    "badge_product_start",
                    extra={
                        "event": "badge_product_start",
                        "sku": sku,
                        "product_id": product_id,
                        "url": url,
                    },
                )

                evidence, err = await process_product_page(browser, sku=sku, url=url)
                if err or evidence is None:
                    summary["failed"] += 1
                    summary["failed_products"].append(
                        {
                            "product_id": product_id,
                            "sku": sku,
                            "url": url,
                            "reason": err or "no_evidence",
                        }
                    )
                    await asyncio.sleep(0.5)
                    continue

                has_dom_evidence = bool(
                    evidence.badge_texts
                    or evidence.img_alts
                    or evidence.img_titles
                    or evidence.element_titles
                    or evidence.element_texts
                )
                if not has_dom_evidence:
                    summary["products_no_badge_evidence"] += 1

                normalized = product_to_normalized(product)
                try:
                    before = db.execute(
                        text("SELECT COUNT(*) FROM badges WHERE collection_run_id = :rid"),
                        {"rid": run_id},
                    ).scalar()
                    evaluation = persister.save_badges(
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
                    # If evaluation produced zero rows (no expected/detected), still
                    # record a provenance row so product-set coverage is auditable.
                    if inserted == 0:
                        from database.repositories import ObservationRepository

                        ObservationRepository(db).add_badge(
                            product_id=product_id,
                            collection_run_id=run_id,
                            observed_at=observed_at,
                            badge_code="no_platform_badge_signal",
                            badge_text="no_expected_or_detected_platform_badges",
                            is_relevant=False,
                            relevance_notes="status=other; reason=no_platform_badge_signals",
                            screenshot_path=evidence.screenshot_path,
                            source_url=evidence.source_url or url,
                        )
                        db.commit()
                        inserted = 1
                        if has_dom_evidence:
                            summary["products_no_badge_evidence"] += 1
                    summary["badge_rows_inserted"] += inserted
                    summary["processed"] += 1
                    summary["processed_product_ids"].append(product_id)
                    logger.info(
                        "badge_product_done",
                        extra={
                            "event": "badge_product_done",
                            "sku": sku,
                            "expected": evaluation.expected,
                            "detected": evaluation.detected,
                            "correct": evaluation.correct,
                            "missing": evaluation.missing,
                            "ambiguous": evaluation.ambiguous,
                            "rows": inserted,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    summary["failed"] += 1
                    summary["failed_products"].append(
                        {
                            "product_id": product_id,
                            "sku": sku,
                            "url": url,
                            "reason": f"persist_error:{exc}",
                        }
                    )
                    logger.exception("badge_persist_failed", extra={"sku": sku})

                await asyncio.sleep(0.6)

        # --- Verification from DB (source of truth) ---
        run_rows = db.execute(
            text(
                """
                SELECT COUNT(*) AS n,
                       COUNT(DISTINCT product_id) AS uniq
                FROM badges
                WHERE collection_run_id = :rid
                """
            ),
            {"rid": run_id},
        ).mappings().one()
        summary["db_badge_rows_for_run"] = int(run_rows["n"] or 0)
        summary["unique_products_audited"] = int(run_rows["uniq"] or 0)

        orphan = db.execute(
            text(
                """
                SELECT COUNT(*) FROM badges b
                LEFT JOIN products p ON p.id = b.product_id
                WHERE b.collection_run_id = :rid AND p.id IS NULL
                """
            ),
            {"rid": run_id},
        ).scalar()
        summary["orphan_badge_rows"] = int(orphan or 0)

        by_code = db.execute(
            text(
                """
                SELECT badge_code, COUNT(*) AS n
                FROM badges
                WHERE collection_run_id = :rid
                GROUP BY badge_code
                ORDER BY n DESC, badge_code
                """
            ),
            {"rid": run_id},
        ).mappings().all()
        summary["by_badge_code"] = {r["badge_code"]: int(r["n"]) for r in by_code}

        status_rows = db.execute(
            text(
                """
                SELECT
                    CASE
                      WHEN relevance_notes ILIKE '%%status=correct%%' THEN 'correct'
                      WHEN relevance_notes ILIKE '%%status=missing%%' THEN 'missing'
                      WHEN relevance_notes ILIKE '%%status=ambiguous%%' THEN 'ambiguous'
                      WHEN relevance_notes ILIKE '%%status=detected%%' THEN 'detected'
                      WHEN relevance_notes ILIKE '%%status=failed%%' THEN 'failed'
                      ELSE 'other'
                    END AS status,
                    COUNT(*) AS n
                FROM badges
                WHERE collection_run_id = :rid
                GROUP BY 1
                ORDER BY 1
                """
            ),
            {"rid": run_id},
        ).mappings().all()
        summary["by_status"] = {r["status"]: int(r["n"]) for r in status_rows}

        audited_ids = {
            int(r)
            for r in db.execute(
                text(
                    """
                    SELECT DISTINCT product_id
                    FROM badges
                    WHERE collection_run_id = :rid
                    """
                ),
                {"rid": run_id},
            ).scalars()
        }
        summary["audited_product_ids"] = sorted(audited_ids)
        extra = sorted(audited_ids - original_ids)
        missing_from_set = sorted(original_ids - audited_ids)
        summary["product_set_extra_ids"] = extra
        summary["product_set_missing_ids"] = missing_from_set
        if extra:
            summary["product_set_match"] = "FAIL"
        elif not missing_from_set and audited_ids == original_ids:
            summary["product_set_match"] = "PASS"
        elif audited_ids.issubset(original_ids):
            summary["product_set_match"] = "PARTIAL"
        else:
            summary["product_set_match"] = "FAIL"

        total_badges = db.execute(text("SELECT COUNT(*) FROM badges")).scalar()
        summary["badges_table_total"] = int(total_badges or 0)
        summary["database_persistence"] = (
            "PASS"
            if summary["db_badge_rows_for_run"] > 0 and summary["orphan_badge_rows"] == 0
            else "FAIL"
        )

        from database.models import CollectionRun

        run_row = db.get(CollectionRun, run_id)
        if run_row is not None:
            CollectionRunRepository(db).complete(
                run_row,
                status="completed" if summary["failed"] == 0 else "partial",
                items_collected=summary["processed"],
                error_message="; ".join(
                    f"{f['sku']}:{f['reason']}" for f in summary["failed_products"][:8]
                )
                or None,
            )
            db.commit()
    finally:
        db.close()
        engine.dispose()

    return summary


def main() -> None:
    summary = asyncio.run(run_badge_collection())
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
