"""Read-only tabular payloads for reports. Reuses existing analytics functions."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from analytics.banner_share import banner_share_by_brand, load_banner_observations
from analytics.banner_share.models import BannerShareScope
from analytics.compliance import compute_brand_scores, load_audit_rows
from analytics.compliance.config import CHECK_CODES
from analytics.pricing import (
    average_price_by_brand,
    compare_by_retailer,
    count_discounted_products,
    list_price_observations,
)
from analytics.pricing.models import PricingScope
from analytics.share_of_shelf import share_of_shelf_by_brand
from analytics.share_of_shelf.models import SosScope
from analytics.share_of_shelf.queries import load_eligible_listings
from analytics.share_of_voice import share_of_voice
from analytics.share_of_voice.models import SovScope
from dashboard.queries.catalog import extract_product_specs
from dashboard.presentation import TRACKED_PLATFORM_BRANDS
from database.models import (
    Badge,
    BannerObservation,
    CollectionRun,
    CollectionRunStep,
    PriceHistory,
    Product,
    ProductSnapshot,
    RetailerAudit,
    SearchObservation,
)
from reporting.run_scope import observation_run_ids_for_run
from reporting.sections import unavailable_rows

NA = "N/A"
EXPECTED_COMPONENTS = (
    "newegg",
    "mercadolibre",
    "audits",
    "badges",
    "pricing",
    "banners",
    "search",
)


def _ratio_pct(value: Optional[float | Decimal]) -> int | str:
    if value is None:
        return NA
    return int(round(float(value) * 100))


def _dec(value: Any) -> str | float:
    if value is None:
        return NA
    if isinstance(value, Decimal):
        return float(value)
    return value


def _merged_check_pct(score, code: str) -> int | str:
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


def _count_for_runs(session: Session, model, run_ids: Sequence[int]) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.collection_run_id.in_(list(run_ids)))
        )
        or 0
    )


def _attribute_coverage_for_snapshots(
    snapshots: Sequence[ProductSnapshot],
) -> list[dict[str, Any]]:
    total = len(snapshots)
    if total == 0:
        return unavailable_rows()

    present = {
        "Processor": 0,
        "Graphics": 0,
        "RAM": 0,
        "Storage": 0,
        "Price": 0,
        "Brand": 0,
    }
    for snap in snapshots:
        specs = extract_product_specs(snap.raw_payload)
        if specs.get("processor"):
            present["Processor"] += 1
        if specs.get("gpu"):
            present["Graphics"] += 1
        if specs.get("ram"):
            present["RAM"] += 1
        if specs.get("storage"):
            present["Storage"] += 1
        if snap.price_amount is not None:
            present["Price"] += 1
        if snap.brand:
            present["Brand"] += 1
    return [
        {
            "attribute": name,
            "present": count,
            "total": total,
            "coverage_percent": int(round(100.0 * count / total)),
        }
        for name, count in present.items()
    ]


def _badge_coverage_for_runs(session: Session, run_ids: Sequence[int]) -> list[dict[str, Any]]:
    from collector.config_loader import load_badges
    from dashboard.presentation import badge_coverage_state

    badge_count = _count_for_runs(session, Badge, run_ids)
    if badge_count <= 0:
        return unavailable_rows()

    families = list(load_badges().get("platform_families") or [])
    rows = session.execute(
        select(Badge.product_id, Badge.badge_code, Product.brand)
        .join(Product, Product.id == Badge.product_id)
        .where(Badge.collection_run_id.in_(list(run_ids)))
    ).all()
    evidence_ids: dict[str, set[int]] = {b: set() for b in TRACKED_PLATFORM_BRANDS}
    family_ids: dict[str, set[int]] = {str(f.get("code")): set() for f in families}
    family_obs: dict[str, int] = {str(f.get("code")): 0 for f in families}
    for product_id, code, brand in rows:
        if brand in evidence_ids:
            evidence_ids[brand].add(product_id)
        if code and code in family_ids:
            family_ids[code].add(product_id)
            family_obs[code] = family_obs.get(code, 0) + 1

    out: list[dict[str, Any]] = []
    for family in families:
        brand = str(family.get("brand") or "")
        code = str(family.get("code") or "")
        name = str(family.get("name") or code)
        inspected = evidence_ids.get(brand) or set()
        if not inspected:
            out.append(
                {
                    "brand": brand,
                    "badge_family": name,
                    "code": code,
                    "observations": family_obs.get(code, 0),
                    "coverage_percent": NA,
                    "status": "N/A",
                }
            )
            continue
        detected = len(family_ids.get(code, set()) & inspected)
        rate = detected / len(inspected)
        out.append(
            {
                "brand": brand,
                "badge_family": name,
                "code": code,
                "observations": family_obs.get(code, 0),
                "coverage_percent": int(round(rate * 100.0)),
                "status": badge_coverage_state(rate),
            }
        )
    return out


def _product_rows_for_runs(
    session: Session,
    run_ids: Sequence[int],
    *,
    eligible_ids: set[int] | None,
) -> list[dict[str, Any]]:
    stmt = (
        select(ProductSnapshot, Product)
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(ProductSnapshot.collection_run_id.in_(list(run_ids)))
        .order_by(Product.retailer_code, Product.retailer_sku, ProductSnapshot.id)
    )
    seen: set[int] = set()
    rows: list[dict[str, Any]] = []
    for snap, product in session.execute(stmt).all():
        if product.id in seen:
            continue
        seen.add(product.id)
        specs = extract_product_specs(snap.raw_payload)
        if eligible_ids is None:
            eligibility = NA
        else:
            eligibility = "eligible" if product.id in eligible_ids else "excluded"
        rows.append(
            {
                "retailer": product.retailer_code,
                "country": product.country_code,
                "product": snap.title or product.title or NA,
                "sku": product.retailer_sku,
                "brand": snap.brand or product.brand or NA,
                "product_type": snap.product_type or product.product_type or NA,
                "processor": specs.get("processor") or NA,
                "gpu": specs.get("gpu") or NA,
                "ram": specs.get("ram") or NA,
                "storage": specs.get("storage") or NA,
                "price": _dec(snap.price_amount),
                "currency": snap.currency or NA,
                "eligibility": eligibility,
            }
        )
    return rows


def _retailer_coverage(
    session: Session,
    run_ids: Sequence[int],
    steps: Sequence[CollectionRunStep],
) -> list[dict[str, Any]]:
    grouped = session.execute(
        select(
            Product.retailer_code,
            Product.country_code,
            func.count(func.distinct(ProductSnapshot.product_id)),
        )
        .join(Product, Product.id == ProductSnapshot.product_id)
        .where(ProductSnapshot.collection_run_id.in_(list(run_ids)))
        .group_by(Product.retailer_code, Product.country_code)
        .order_by(Product.retailer_code, Product.country_code)
    ).all()
    if not grouped:
        return unavailable_rows()
    step_by_component = {step.component: step.status for step in steps}
    out = []
    for retailer, country, count in grouped:
        out.append(
            {
                "retailer": retailer,
                "country": country,
                "products": int(count),
                "status": step_by_component.get(str(retailer), NA),
            }
        )
    return out


def _collection_status(
    run: CollectionRun, steps: Sequence[CollectionRunStep]
) -> list[dict[str, Any]]:
    by_component = {step.component: step for step in steps}
    meta = run.run_metadata if isinstance(run.run_metadata, dict) else {}
    declared = meta.get("step_statuses") if isinstance(meta.get("step_statuses"), dict) else {}
    rows = []
    for component in EXPECTED_COMPONENTS:
        step = by_component.get(component)
        if step is None:
            rows.append(
                {
                    "component": component,
                    "status": declared.get(component) or "NOT_AVAILABLE",
                    "records": NA,
                }
            )
        else:
            rows.append(
                {
                    "component": component,
                    "status": step.status,
                    "records": step.records_processed,
                }
            )
    extra = [s for s in steps if s.component not in EXPECTED_COMPONENTS]
    for step in extra:
        rows.append(
            {
                "component": step.component,
                "status": step.status,
                "records": step.records_processed,
            }
        )
    return rows


def _promotion_summary(observations) -> list[dict[str, Any]]:
    buckets: dict[str, list] = defaultdict(list)
    for obs in observations:
        brand = obs.brand or "UNKNOWN"
        buckets[brand].append(obs)
    if not buckets:
        return unavailable_rows()
    out = []
    for brand in sorted(buckets):
        items = buckets[brand]
        promo = [o for o in items if o.is_on_promotion]
        discounted = [
            o
            for o in items
            if o.is_on_promotion or (o.discount_pct is not None and o.discount_pct > 0)
        ]
        discounts = [float(o.discount_pct) for o in items if o.discount_pct is not None]
        out.append(
            {
                "brand": brand,
                "products_with_promotion": len({o.product_id for o in promo}),
                "discounted_products": len({o.product_id for o in discounted}),
                "average_discount": round(mean(discounts), 4) if discounts else NA,
            }
        )
    return out


def _banner_rows(session: Session, scope: BannerShareScope, snapshot) -> list[dict[str, Any]]:
    extras: dict[str, dict[str, int]] = defaultdict(
        lambda: {"linked_count": 0, "discount_count": 0, "badge_count": 0}
    )
    for obs in load_banner_observations(session, scope=scope):
        brand = obs.brand_detected or "UNKNOWN"
        if obs.link_present:
            extras[brand]["linked_count"] += 1
        if obs.discount_text:
            extras[brand]["discount_count"] += 1
        if obs.badge_text:
            extras[brand]["badge_count"] += 1
    rows = []
    for row in snapshot.shares:
        extra = extras.get(row.brand, {})
        rows.append(
            {
                "brand": row.brand,
                "banner_count": row.banner_count,
                "tracked_brand_count": row.total_tracked_banners,
                "linked_count": extra.get("linked_count", 0),
                "discount_count": extra.get("discount_count", 0),
                "badge_count": extra.get("badge_count", 0),
                "share_percent": _ratio_pct(row.banner_share),
                "retailer": row.retailer_code or NA,
            }
        )
    return rows


def build_report_tables(
    session: Session,
    *,
    run: CollectionRun,
    steps: Sequence[CollectionRunStep],
) -> dict[str, list[dict[str, Any]]]:
    """Assemble sheet/PSV rows from existing analytics, scoped to ``run``."""
    run_ids = observation_run_ids_for_run(session, int(run.id))
    sos_scope = SosScope(collection_run_ids=run_ids)
    sov_scope = SovScope(collection_run_ids=run_ids)
    price_scope = PricingScope(collection_run_ids=run_ids)
    banner_scope = BannerShareScope(collection_run_ids=run_ids)

    snapshot_count = _count_for_runs(session, ProductSnapshot, run_ids)
    audit_count = _count_for_runs(session, RetailerAudit, run_ids)
    search_count = _count_for_runs(session, SearchObservation, run_ids)
    price_count = _count_for_runs(session, PriceHistory, run_ids)
    banner_count = _count_for_runs(session, BannerObservation, run_ids)
    badge_count = _count_for_runs(session, Badge, run_ids)

    audit_rows = (
        load_audit_rows(session, collection_run_ids=run_ids) if audit_count else []
    )
    brand_scores = compute_brand_scores(audit_rows) if audit_rows else {}
    sos = (
        share_of_shelf_by_brand(session, scope=sos_scope)
        if snapshot_count or search_count
        else None
    )
    eligible_ids: set[int] | None = None
    if sos is not None:
        listings, _exclusions, _runs = load_eligible_listings(session, scope=sos_scope)
        eligible_ids = {item.product_id for item in listings}
    sov = share_of_voice(session, scope=sov_scope) if search_count else None
    banners = banner_share_by_brand(session, scope=banner_scope) if banner_count else None
    prices = average_price_by_brand(session, scope=price_scope) if price_count else []
    discounted = count_discounted_products(session, scope=price_scope) if price_count else None
    by_retailer = compare_by_retailer(session, scope=price_scope) if price_count else []
    observations = list_price_observations(session, scope=price_scope) if price_count else []

    snapshots = list(
        session.scalars(
            select(ProductSnapshot).where(ProductSnapshot.collection_run_id.in_(list(run_ids)))
        ).all()
    )
    distinct_products = int(
        session.scalar(
            select(func.count(func.distinct(ProductSnapshot.product_id))).where(
                ProductSnapshot.collection_run_id.in_(list(run_ids))
            )
        )
        or 0
    )
    distinct_retailers = int(
        session.scalar(
            select(func.count(func.distinct(Product.retailer_code)))
            .select_from(ProductSnapshot)
            .join(Product, Product.id == ProductSnapshot.product_id)
            .where(ProductSnapshot.collection_run_id.in_(list(run_ids)))
        )
        or 0
    )

    details = [
        {"metric": "collection_run_id", "value": run.id},
        {"metric": "run_type", "value": run.run_type},
        {"metric": "status", "value": run.status},
        {"metric": "retailer_code", "value": run.retailer_code or NA},
        {"metric": "country_code", "value": run.country_code or NA},
        {
            "metric": "started_at",
            "value": run.started_at.isoformat() if run.started_at else NA,
        },
        {
            "metric": "completed_at",
            "value": run.completed_at.isoformat() if run.completed_at else NA,
        },
        {"metric": "items_collected", "value": run.items_collected},
        {
            "metric": "observation_run_ids",
            "value": ",".join(str(rid) for rid in run_ids),
        },
        {
            "metric": "generated_at",
            "value": datetime.now(timezone.utc).isoformat(),
        },
    ]

    executive = [
        {"metric": "products_observed", "value": distinct_products or NA},
        {"metric": "retailers", "value": distinct_retailers or NA},
        {"metric": "audit_observations", "value": audit_count if audit_count else NA},
        {"metric": "badge_observations", "value": badge_count if badge_count else NA},
        {"metric": "price_observations", "value": price_count if price_count else NA},
        {
            "metric": "discounted_products",
            "value": discounted if discounted is not None else NA,
        },
        {
            "metric": "shelf_universe_size",
            "value": sos.universe_size if sos is not None else NA,
        },
        {
            "metric": "search_tracked_appearances",
            "value": sov.tracked_appearances if sov is not None else NA,
        },
        {
            "metric": "banner_tracked_count",
            "value": banners.total_tracked_banners if banners is not None else NA,
        },
        {"metric": "run_status", "value": run.status},
    ]

    if sos is None:
        shelf = unavailable_rows()
    else:
        shelf = [
            {
                "brand": row.value,
                "product_count": row.product_count,
                "universe_size": row.universe_size,
                "share_percent": _ratio_pct(row.share),
            }
            for row in sos.shares
        ]

    if sov is None:
        visibility = unavailable_rows()
    else:
        visibility = [
            {
                "brand": row.brand,
                "appearances": row.appearances,
                "share_of_voice": _ratio_pct(row.share_of_voice),
                "average_rank": _dec(row.average_rank),
                "coverage_status": row.collection_basis,
                "stratum": row.stratum or NA,
            }
            for row in sov.metrics
        ]

    if not price_count:
        pricing: list[dict[str, Any]] = unavailable_rows()
        promotions = unavailable_rows()
        promotion_details = unavailable_rows()
    else:
        pricing = [
            {
                "brand": row.value,
                "observations": row.product_count,
                "average_price": _dec(row.average_price),
                "median_price": _dec(row.median_price),
                "currency": row.currency,
                "discounted_products": row.discounted_product_count,
                "average_discount": _dec(row.average_discount_pct),
            }
            for row in prices
        ]
        for row in by_retailer:
            pricing.append(
                {
                    "brand": NA,
                    "retailer": row.value,
                    "observations": row.product_count,
                    "average_price": _dec(row.average_price),
                    "median_price": _dec(row.median_price),
                    "currency": row.currency,
                    "discounted_products": row.discounted_product_count,
                    "average_discount": _dec(row.average_discount_pct),
                }
            )
        promotions = _promotion_summary(observations)
        promotion_details = [
            {
                "product_id": obs.product_id,
                "brand": obs.brand or NA,
                "retailer": obs.retailer_code,
                "currency": obs.currency,
                "current_price": _dec(obs.current_price),
                "original_price": _dec(obs.original_price),
                "discount_pct": _dec(obs.discount_pct),
                "is_on_promotion": obs.is_on_promotion,
                "promotion_text": obs.promotion_text or NA,
                "observed_at": obs.observed_at.isoformat() if obs.observed_at else NA,
            }
            for obs in observations
            if obs.is_on_promotion or (obs.discount_pct is not None and obs.discount_pct > 0)
        ]

    if not audit_count:
        compliance = unavailable_rows()
    else:
        compliance = []
        for brand, score in brand_scores.items():
            row = {"brand": brand}
            for code in CHECK_CODES:
                row[code.lower()] = _merged_check_pct(score, code)
            row["notebook"] = _ratio_pct(score.notebook.score if score.notebook else None)
            row["desktop"] = _ratio_pct(score.desktop.score if score.desktop else None)
            row["overall"] = _ratio_pct(score.overall_score)
            compliance.append(row)

    banner_rows = (
        _banner_rows(session, banner_scope, banners) if banners is not None else unavailable_rows()
    )
    quality = _attribute_coverage_for_snapshots(snapshots)
    badges = _badge_coverage_for_runs(session, run_ids)
    product_data = _product_rows_for_runs(session, run_ids, eligible_ids=eligible_ids)
    if not product_data:
        product_data = unavailable_rows()

    return {
        "executive": executive,
        "details": details,
        "coverage": _retailer_coverage(session, run_ids, steps),
        "status": _collection_status(run, steps),
        "shelf": shelf,
        "visibility": visibility,
        "pricing": pricing,
        "promotions": promotions,
        "promotion_details": promotion_details,
        "compliance": compliance,
        "banners": banner_rows,
        "quality": quality,
        "badges": badges,
        "products": product_data,
    }
