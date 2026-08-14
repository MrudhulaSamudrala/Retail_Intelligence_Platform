"""Read-only tabular payloads for reports. Reuses existing analytics functions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from analytics.banner_share import banner_share_by_brand
from analytics.compliance import compute_brand_scores, load_audit_rows
from analytics.compliance.config import CHECK_CODES
from analytics.pricing import (
    average_price_by_brand,
    compare_by_retailer,
    count_discounted_products,
    list_price_observations,
)
from analytics.share_of_shelf import share_of_shelf_by_brand
from analytics.share_of_voice import share_of_voice
from dashboard.filters import default_filters
from dashboard.queries.catalog import attribute_coverage, badge_coverage_matrix
from database.models import CollectionRun, CollectionRunStep, Product

NA = ""


def _ratio_pct(value: Optional[float | Decimal]) -> str:
    if value is None:
        return NA
    return str(int(round(float(value) * 100)))


def _dec(value: Any) -> str:
    if value is None:
        return NA
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _merged_check_pct(score, code: str) -> str:
    parts = []
    for segment in (score.notebook, score.desktop):
        if segment is None:
            continue
        check = (segment.check_scores or {}).get(code)
        if check is not None:
            parts.append(check.coverage)
    pass_count = sum(p.pass_count for p in parts)
    fail_count = sum(p.fail_count for p in parts)
    scored = pass_count + fail_count
    if scored <= 0:
        return NA
    return _ratio_pct(pass_count / scored)


def build_report_tables(
    session: Session,
    *,
    run: CollectionRun,
    steps: Sequence[CollectionRunStep],
) -> dict[str, list[dict[str, Any]]]:
    """Assemble sheet/PSV rows from existing analytics. Does not write to the DB."""
    audit_rows = load_audit_rows(session)
    brand_scores = compute_brand_scores(audit_rows)
    sos = share_of_shelf_by_brand(session)
    sov = share_of_voice(session)
    banners = banner_share_by_brand(session)
    prices = average_price_by_brand(session)
    discounted = count_discounted_products(session)
    by_retailer = compare_by_retailer(session)
    observations = list_price_observations(session)
    quality = attribute_coverage(session, filters=default_filters())
    badges = badge_coverage_matrix(session)

    executive = [
        {
            "field": "collection_run_id",
            "value": run.id,
        },
        {
            "field": "status",
            "value": run.status,
        },
        {
            "field": "started_at",
            "value": run.started_at.isoformat() if run.started_at else NA,
        },
        {
            "field": "completed_at",
            "value": run.completed_at.isoformat() if run.completed_at else NA,
        },
        {
            "field": "items_collected",
            "value": run.items_collected,
        },
    ]
    for step in steps:
        executive.append(
            {
                "field": f"component_{step.component}",
                "value": step.status,
                "records_processed": step.records_processed,
            }
        )
    executive.append(
        {
            "field": "shelf_universe_size",
            "value": sos.universe_size,
            "collection_status": sos.collection_status,
        }
    )
    executive.append(
        {
            "field": "search_tracked_appearances",
            "value": sov.tracked_appearances,
            "collection_basis": sov.collection_basis,
        }
    )
    executive.append(
        {
            "field": "banner_tracked_count",
            "value": banners.total_tracked_banners,
        }
    )
    executive.append(
        {
            "field": "discounted_products",
            "value": discounted,
        }
    )

    shelf = [
        {
            "brand": row.value,
            "product_count": row.product_count,
            "universe_size": row.universe_size,
            "share": _dec(row.share),
        }
        for row in sos.shares
    ]

    visibility = [
        {
            "brand": row.brand,
            "appearances": row.appearances,
            "share_of_voice": _dec(row.share_of_voice),
            "average_rank": _dec(row.average_rank),
            "collection_basis": row.collection_basis,
            "stratum": row.stratum or NA,
        }
        for row in sov.metrics
    ]

    pricing = [
        {
            "brand": row.value,
            "currency": row.currency,
            "product_count": row.product_count,
            "average_price": _dec(row.average_price),
            "median_price": _dec(row.median_price),
            "average_discount_pct": _dec(row.average_discount_pct),
            "discounted_product_count": row.discounted_product_count,
        }
        for row in prices
    ]
    for row in by_retailer:
        pricing.append(
            {
                "brand": NA,
                "retailer": row.value,
                "currency": row.currency,
                "product_count": row.product_count,
                "average_price": _dec(row.average_price),
                "median_price": _dec(row.median_price),
                "average_discount_pct": _dec(row.average_discount_pct),
                "discounted_product_count": row.discounted_product_count,
            }
        )

    promotions = [
        {
            "product_id": obs.product_id,
            "brand": obs.brand or NA,
            "retailer_code": obs.retailer_code,
            "currency": obs.currency,
            "current_price": _dec(obs.current_price),
            "original_price": _dec(obs.original_price),
            "discount_pct": _dec(obs.discount_pct),
            "is_on_promotion": obs.is_on_promotion,
            "promotion_text": obs.promotion_text or NA,
            "observed_at": obs.observed_at.isoformat() if obs.observed_at else NA,
        }
        for obs in observations
        if obs.is_on_promotion or (obs.discount_pct or 0) > 0
    ]

    compliance = []
    for brand, score in brand_scores.items():
        row = {"brand": brand}
        for code in CHECK_CODES:
            row[code.lower()] = _merged_check_pct(score, code)
        row["notebook"] = _ratio_pct(score.notebook.score if score.notebook else None)
        row["desktop"] = _ratio_pct(score.desktop.score if score.desktop else None)
        row["overall"] = _ratio_pct(score.overall_score)
        compliance.append(row)

    banner_rows = [
        {
            "brand": row.brand,
            "banner_count": row.banner_count,
            "total_tracked_banners": row.total_tracked_banners,
            "banner_share": _dec(row.banner_share),
            "retailer_code": row.retailer_code or NA,
        }
        for row in banners.shares
    ]

    products = list(
        session.scalars(
            select(Product)
            .where(Product.is_active.is_(True))
            .order_by(Product.retailer_code, Product.retailer_sku)
        ).all()
    )
    product_data = [
        {
            "id": product.id,
            "retailer_code": product.retailer_code,
            "country_code": product.country_code,
            "retailer_sku": product.retailer_sku,
            "title": product.title or NA,
            "brand": product.brand or NA,
            "oem": product.oem or NA,
            "product_type": product.product_type or NA,
            "canonical_url": product.canonical_url,
        }
        for product in products
    ]

    return {
        "executive": executive,
        "shelf": shelf,
        "visibility": visibility,
        "pricing": pricing,
        "promotions": promotions,
        "compliance": compliance,
        "banners": banner_rows,
        "quality": quality,
        "badges": badges,
        "products": product_data,
        "meta": [
            {
                "collection_run_id": run.id,
                "status": run.status,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "product_count": int(
                    session.scalar(select(func.count()).select_from(Product)) or 0
                ),
            }
        ],
    }
