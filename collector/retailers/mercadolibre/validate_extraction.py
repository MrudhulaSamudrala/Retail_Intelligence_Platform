"""Controlled Mercado Libre extraction validation (5–10 products, not 100).

Usage:
  python -m collector.retailers.mercadolibre.validate_extraction --limit 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from typing import Any

from dotenv import load_dotenv

from collector.logging_utils import setup_logging
from collector.pipeline import CollectionPipeline
from collector.retailers.mercadolibre import build_collector
from database.connection import get_engine, get_session_factory


def _product_report(product) -> dict[str, Any]:
    raw = product.raw_payload or {}
    specs = raw.get("specs") or {}
    prov = raw.get("field_provenance") or {}
    fields = prov.get("fields") or {}
    sources = sorted(
        {
            str(item.get("source"))
            for item in fields.values()
            if isinstance(item, dict) and item.get("source")
        }
    )
    methods = sorted(
        {
            str(item.get("extraction_method"))
            for item in fields.values()
            if isinstance(item, dict) and item.get("extraction_method")
        }
    )
    evidence = raw.get("evidence") or {}
    surfaces = evidence.get("surfaces") or {}
    pdp = surfaces.get("pdp") or {}
    return {
        "title": product.title,
        "sku": product.retailer_sku,
        "oem": product.oem,
        "platform": product.brand or raw.get("platform_brand"),
        "product_type": product.product_type,
        "gaming_relevance": raw.get("gaming_relevance"),
        "processor": product.processor,
        "price": str(product.price_amount) if product.price_amount is not None else None,
        "discount": str(product.discount_pct) if product.discount_pct is not None else None,
        "promotions": product.promo_text,
        "badges": (raw.get("badge_signals") or {}).get("badge_texts")
        or (raw.get("badge_signals") or {}).get("img_alts"),
        "specifications_extracted": len(specs) if isinstance(specs, dict) else 0,
        "evidence_completeness": raw.get("evidence_completeness")
        or evidence.get("overall_status"),
        "extraction_sources": sources,
        "extraction_methods": methods,
        "access_status": pdp.get("status") or raw.get("detail_page_status"),
        "access_reason": pdp.get("reason") or raw.get("detail_page_status"),
        "unknown_fields": raw.get("unknown_fields") or {},
        "pdp_accessible": raw.get("pdp_accessible"),
        "json_ld": raw.get("json_ld"),
        "embedded_json": raw.get("embedded_json"),
        "network_json": raw.get("network_json"),
        "layers_attempted": raw.get("layers_attempted") or prov.get("layers_attempted"),
        "url": product.source_url,
    }


async def run_validation(limit: int) -> dict[str, Any]:
    setup_logging()
    load_dotenv()
    engine = get_engine()
    factory = get_session_factory(engine)
    session = factory()
    try:
        collector = build_collector()
        pipeline = CollectionPipeline(session, collector)
        outcome = await pipeline.run(limit=limit)
        reports = [_product_report(p) for p in outcome.success]
        access = Counter(str(r.get("access_status")) for r in reports)
        listing_only = sum(1 for r in reports if r.get("pdp_accessible") is False)
        pdp_ok = sum(1 for r in reports if r.get("pdp_accessible") is True)
        json_ld = sum(1 for r in reports if r.get("json_ld"))
        embedded = sum(1 for r in reports if r.get("embedded_json"))
        network = sum(1 for r in reports if r.get("network_json"))
        unknown_counts: Counter[str] = Counter()
        for report in reports:
            for key in (report.get("unknown_fields") or {}):
                unknown_counts[key] += 1
        extracted_counts: Counter[str] = Counter()
        for product in outcome.success:
            if product.title:
                extracted_counts["title"] += 1
            if product.processor:
                extracted_counts["processor"] += 1
            if product.price_amount is not None:
                extracted_counts["price"] += 1
            if product.oem and product.oem != "UNKNOWN":
                extracted_counts["oem"] += 1
            if product.brand and product.brand != "UNKNOWN":
                extracted_counts["platform"] += 1
            if product.ram:
                extracted_counts["ram"] += 1
            if product.storage:
                extracted_counts["storage"] += 1
            if product.gpu:
                extracted_counts["gpu"] += 1
        return {
            "limit": limit,
            "discovered": outcome.discovered,
            "collected": len(outcome.success),
            "failed": outcome.failed,
            "skipped_irrelevant": len(outcome.skipped_irrelevant),
            "skipped_duplicates": len(outcome.skipped_duplicates),
            "collection_run_id": outcome.collection_run_id,
            "run_status": outcome.status,
            "products": reports,
            "summary": {
                "access_status": dict(access),
                "listing_only_products": listing_only,
                "pdp_accessible_products": pdp_ok,
                "structured_data_extraction": json_ld,
                "embedded_data_extraction": embedded,
                "network_data_extraction": network,
                "unknown_field_frequency": dict(unknown_counts),
                "successfully_extracted_fields": dict(extracted_counts),
            },
        }
    finally:
        session.close()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="ML extraction quality validation")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    result = asyncio.run(run_validation(args.limit))
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
