"""Controlled API enrichment of existing Mercado Libre SKUs (5–10).

Does not run the 100-product universe. Does not persist when the API is disabled.

  python -m collector.retailers.mercadolibre.api.validate --limit 8
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select, text

from collector.logging_utils import setup_logging
from collector.normalize import NormalizedProduct
from collector.persist import CollectionPersister
from collector.retailers.mercadolibre.api.client import MercadoLibreApiClient
from collector.retailers.mercadolibre.api.config import load_api_config
from collector.retailers.mercadolibre.api.enrich import enrich_product
from collector.retailers.mercadolibre.product_page import build_from_listing
from database.connection import get_engine, get_session_factory
from database.models import Badge, PriceHistory, Product, ProductSnapshot, RetailerAudit


def db_safety_counts(session) -> dict[str, Any]:
    ml_skus = list(
        session.scalars(
            select(Product.retailer_sku).where(Product.retailer_code == "mercadolibre")
        )
    )
    dupes = [sku for sku, n in Counter(ml_skus).items() if n > 1]
    return {
        "newegg_products": int(
            session.scalar(
                select(func.count()).select_from(Product).where(Product.retailer_code == "newegg")
            )
            or 0
        ),
        "mercadolibre_products": int(
            session.scalar(
                select(func.count()).select_from(Product).where(
                    Product.retailer_code == "mercadolibre"
                )
            )
            or 0
        ),
        "unique_ml_skus": len(set(ml_skus)),
        "duplicate_ml_skus": dupes,
        "snapshots": int(session.scalar(select(func.count()).select_from(ProductSnapshot)) or 0),
        "price_history": int(session.scalar(select(func.count()).select_from(PriceHistory)) or 0),
        "audits": int(session.scalar(select(func.count()).select_from(RetailerAudit)) or 0),
        "badges": int(session.scalar(select(func.count()).select_from(Badge)) or 0),
    }


def _snapshot_to_product(row: Product, snap: ProductSnapshot | None) -> NormalizedProduct:
    raw = {}
    if snap is not None and isinstance(snap.raw_payload, dict):
        raw = dict(snap.raw_payload)
    title = (snap.title if snap else None) or row.title
    price_text = None
    list_text = None
    if snap and snap.price_amount is not None:
        price_text = str(snap.price_amount)
    if snap and snap.list_price is not None:
        list_text = str(snap.list_price)
    extra = {
        k: v
        for k, v in raw.items()
        if k
        not in {
            "field_provenance",
            "evidence",
            "api_status",
            "api_lookup",
            "field_conflicts",
        }
    }
    product = build_from_listing(
        retailer_code=row.retailer_code,
        country_code=row.country_code,
        currency=(snap.currency if snap else None) or "BRL",
        sku=row.retailer_sku,
        source_url=row.canonical_url,
        title=title,
        price_text=price_text,
        list_price_text=list_text,
        promo_text=snap.promo_text if snap else None,
        category_raw=row.category_raw,
        detail_page_status=str(raw.get("detail_page_status") or "listing_only"),
        extra_raw=extra,
    )
    product.retailer_sku = row.retailer_sku
    return product


def _field_report(product: NormalizedProduct) -> dict[str, Any]:
    raw = product.raw_payload or {}
    fields = (raw.get("field_provenance") or {}).get("fields") or {}
    identity = raw.get("identity") or {}

    def src(name: str) -> str | None:
        item = fields.get(name) or {}
        return item.get("source") if isinstance(item, dict) else None

    evidence = raw.get("evidence") or {}
    surfaces = evidence.get("surfaces") or {}
    return {
        "sku": product.retailer_sku,
        "title": product.title,
        "oem": product.oem,
        "model": identity.get("model"),
        "processor": product.processor,
        "platform": product.brand,
        "ram": product.ram,
        "storage": product.storage,
        "gpu": product.gpu,
        "price": str(product.price_amount) if product.price_amount is not None else None,
        "original_price": str(product.list_price) if product.list_price is not None else None,
        "discount": str(product.discount_pct) if product.discount_pct is not None else None,
        "gtin": identity.get("gtin"),
        "mpn": identity.get("mpn"),
        "sources": {k: src(k) for k in (
            "title",
            "processor",
            "ram",
            "storage",
            "gpu",
            "display",
            "operating_system",
            "gtin",
            "mpn",
            "model",
            "oem_raw",
            "price",
            "list_price",
            "promo_text",
        )},
        "api_status": raw.get("api_status"),
        "listing_status": (surfaces.get("listing") or {}).get("status"),
        "pdp_status": (surfaces.get("pdp") or {}).get("status"),
        "api_surface": surfaces.get("api"),
        "conflicts": raw.get("field_conflicts") or [],
    }


def _filled(report: dict[str, Any]) -> set[str]:
    keys = (
        "title",
        "oem",
        "model",
        "processor",
        "platform",
        "ram",
        "storage",
        "gpu",
        "price",
        "original_price",
        "gtin",
        "mpn",
    )
    return {k for k in keys if report.get(k) not in (None, "", "UNKNOWN")}


def run_validation(limit: int) -> dict[str, Any]:
    setup_logging()
    load_dotenv()
    engine = get_engine()
    factory = get_session_factory(engine)
    session = factory()
    cfg = load_api_config()
    before = db_safety_counts(session)
    summary: dict[str, Any] = {
        "api_enabled": cfg.enabled,
        "before": before,
        "products": [],
        "persisted": False,
    }
    try:
        rows = session.execute(
            text(
                """
                SELECT id, retailer_sku, title, brand, oem, product_type, canonical_url,
                       retailer_code, country_code, category_raw
                FROM products
                WHERE retailer_code = 'mercadolibre' AND country_code = 'BR'
                ORDER BY id
                LIMIT :lim
                """
            ),
            {"lim": limit},
        ).mappings().all()
        if not rows:
            summary["error"] = "no_mercadolibre_products"
            summary["after"] = before
            return summary

        client = MercadoLibreApiClient(cfg)
        persister = None
        run_id = None
        if cfg.enabled:
            persister = CollectionPersister(session)
            run = persister.start_run(
                retailer_code="mercadolibre",
                country_code="BR",
                run_type="api_enrichment",
            )
            session.commit()
            run_id = run.id

        added_fields: Counter[str] = Counter()
        for row in rows:
            product_row = session.get(Product, row["id"])
            snap = session.scalars(
                select(ProductSnapshot)
                .where(ProductSnapshot.product_id == row["id"])
                .order_by(ProductSnapshot.observed_at.desc())
            ).first()
            before_prod = _snapshot_to_product(product_row, snap)
            before_rep = _field_report(before_prod)
            after_prod = enrich_product(before_prod, client=client)
            after_rep = _field_report(after_prod)
            gained = sorted(_filled(after_rep) - _filled(before_rep))
            for name in gained:
                added_fields[name] += 1
            if persister is not None and run_id is not None:
                persister.save_product(
                    after_prod,
                    collection_run_id=run_id,
                    observed_at=datetime.now(timezone.utc),
                )
                session.commit()
                summary["persisted"] = True
            summary["products"].append(
                {
                    "before": before_rep,
                    "after": after_rep,
                    "fields_added_by_api": gained,
                }
            )

        after = db_safety_counts(session)
        summary["after"] = after
        summary["collection_run_id"] = run_id
        summary["additional_fields_frequency"] = dict(added_fields)
        summary["safety"] = {
            "newegg_unchanged": after["newegg_products"] == before["newegg_products"],
            "ml_count_unchanged": after["mercadolibre_products"]
            == before["mercadolibre_products"],
            "no_duplicate_ml_skus": after["duplicate_ml_skus"] == [],
            "snapshots_not_deleted": after["snapshots"] >= before["snapshots"],
            "prices_not_deleted": after["price_history"] >= before["price_history"],
            "audits_not_deleted": after["audits"] >= before["audits"],
            "badges_not_deleted": after["badges"] >= before["badges"],
        }
        return summary
    finally:
        session.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="ML official API enrichment validation")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(run_validation(args.limit), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
