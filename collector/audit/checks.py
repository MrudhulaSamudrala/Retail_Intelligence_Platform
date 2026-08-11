"""Individual retailer audit checks S1, S2, P1–P5.

Each check independently returns PASS / FAIL / UNKNOWN with evidence.
Missing information is NEVER treated as PASS.
"""

from __future__ import annotations

from typing import Optional

from collector.audit.models import (
    FAIL,
    PASS,
    UNKNOWN,
    AuditCheckResult,
    AuditContext,
)
from collector.audit.signals import (
    brand_badge_match,
    brand_or_processor_in_text,
    brand_rich_media_match,
    is_auditable_brand,
    is_auditable_oem,
    oem_rich_media_match,
    specs_to_text,
)


def _unknown(
    code: str,
    reason: str,
    *,
    evidence_text: Optional[str] = None,
    source_url: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditCheckResult:
    payload = {"reason": reason}
    if details:
        payload.update(details)
    return AuditCheckResult(
        check_code=code,
        result=UNKNOWN,
        evidence_text=evidence_text,
        source_url=source_url,
        screenshot_path=screenshot_path,
        details=payload,
    )


def _pass_or_fail(
    code: str,
    *,
    ok: bool,
    evidence_text: Optional[str],
    source_url: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    details: Optional[dict] = None,
) -> AuditCheckResult:
    return AuditCheckResult(
        check_code=code,
        result=PASS if ok else FAIL,
        evidence_text=evidence_text,
        source_url=source_url,
        screenshot_path=screenshot_path,
        details=details or {},
    )


def evaluate_s1(ctx: AuditContext) -> AuditCheckResult:
    """S1: Listing page title includes brand name and/or brand-specific processor line."""
    listing = ctx.listing
    if listing is None or not listing.available:
        return _unknown("S1", "listing_evidence_unavailable")
    if not is_auditable_brand(ctx.brand):
        return _unknown(
            "S1",
            "brand_unknown_cannot_audit",
            evidence_text=listing.title,
            source_url=listing.source_url,
            screenshot_path=listing.screenshot_path,
        )
    if not listing.title:
        return _unknown(
            "S1",
            "listing_title_missing",
            source_url=listing.source_url,
            screenshot_path=listing.screenshot_path,
            details={"selectors": listing.selectors_used},
        )

    match = brand_or_processor_in_text(ctx.brand or "", listing.title)
    details = {
        "brand": ctx.brand,
        "title": listing.title,
        **match,
        "selectors": listing.selectors_used,
    }
    evidence = listing.title
    if match["matched"]:
        evidence = (
            f"title={listing.title!r}; "
            f"brand_name={match['brand_name_match']!r}; "
            f"processor_line={match['processor_line_match']!r}"
        )
    return _pass_or_fail(
        "S1",
        ok=bool(match["matched"]),
        evidence_text=evidence,
        source_url=listing.source_url,
        screenshot_path=listing.screenshot_path,
        details=details,
    )


def evaluate_s2(ctx: AuditContext) -> AuditCheckResult:
    """S2: Brand badge is present on the listing tile."""
    listing = ctx.listing
    if listing is None or not listing.available:
        return _unknown("S2", "listing_evidence_unavailable")
    if not is_auditable_brand(ctx.brand):
        return _unknown(
            "S2",
            "brand_unknown_cannot_audit",
            source_url=listing.source_url,
            screenshot_path=listing.screenshot_path,
        )

    badges_inspected = bool(listing.badge_texts) or listing.tile_text is not None
    if not badges_inspected:
        return _unknown(
            "S2",
            "listing_badge_evidence_missing",
            source_url=listing.source_url,
            screenshot_path=listing.screenshot_path,
            details={"selectors": listing.selectors_used},
        )

    hit = brand_badge_match(
        ctx.brand or "",
        badge_texts=listing.badge_texts,
        page_text=listing.tile_text,
    )
    details = {
        "brand": ctx.brand,
        "badge_texts": listing.badge_texts,
        **hit,
        "selectors": listing.selectors_used,
    }
    evidence = hit.get("evidence") or (
        "; ".join(listing.badge_texts) if listing.badge_texts else listing.tile_text
    )
    return _pass_or_fail(
        "S2",
        ok=bool(hit.get("matched")),
        evidence_text=evidence,
        source_url=listing.source_url,
        screenshot_path=listing.screenshot_path,
        details=details,
    )


def evaluate_p1(ctx: AuditContext) -> AuditCheckResult:
    """P1: Product page title includes brand name, processor line or generation."""
    product = ctx.product
    if product is None or not product.available:
        return _unknown("P1", "product_evidence_unavailable")
    if not is_auditable_brand(ctx.brand):
        return _unknown(
            "P1",
            "brand_unknown_cannot_audit",
            evidence_text=product.title,
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
        )
    if not product.title:
        return _unknown(
            "P1",
            "product_title_missing",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
            details={"selectors": product.selectors_used},
        )

    match = brand_or_processor_in_text(ctx.brand or "", product.title)
    details = {
        "brand": ctx.brand,
        "title": product.title,
        **match,
        "selectors": product.selectors_used,
    }
    evidence = product.title
    if match["matched"]:
        evidence = (
            f"title={product.title!r}; "
            f"brand_name={match['brand_name_match']!r}; "
            f"processor_line={match['processor_line_match']!r}"
        )
    return _pass_or_fail(
        "P1",
        ok=bool(match["matched"]),
        evidence_text=evidence,
        source_url=product.source_url,
        screenshot_path=product.screenshot_path,
        details=details,
    )


def evaluate_p2(ctx: AuditContext) -> AuditCheckResult:
    """P2: Brand badge is present on the product page."""
    product = ctx.product
    if product is None or not product.available:
        return _unknown("P2", "product_evidence_unavailable")
    if not is_auditable_brand(ctx.brand):
        return _unknown(
            "P2",
            "brand_unknown_cannot_audit",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
        )
    if not product.badges_inspected and not product.badge_texts and product.page_text is None:
        return _unknown(
            "P2",
            "product_badge_evidence_missing",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
            details={"selectors": product.selectors_used},
        )

    hit = brand_badge_match(
        ctx.brand or "",
        badge_texts=product.badge_texts,
        page_text=product.page_text if product.badges_inspected or product.badge_texts else None,
    )
    # If badges were inspected (even empty) or badge texts provided, allow FAIL.
    inspected = product.badges_inspected or bool(product.badge_texts) or product.page_text is not None
    if not inspected:
        return _unknown(
            "P2",
            "product_badge_evidence_missing",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
        )

    details = {
        "brand": ctx.brand,
        "badge_texts": product.badge_texts,
        **hit,
        "selectors": product.selectors_used,
    }
    evidence = hit.get("evidence") or (
        "; ".join(product.badge_texts) if product.badge_texts else None
    )
    return _pass_or_fail(
        "P2",
        ok=bool(hit.get("matched")),
        evidence_text=evidence,
        source_url=product.source_url,
        screenshot_path=product.screenshot_path,
        details=details,
    )


def evaluate_p3(ctx: AuditContext) -> AuditCheckResult:
    """P3: Brand or processor line appears in the specification table."""
    product = ctx.product
    if product is None or not product.available:
        return _unknown("P3", "product_evidence_unavailable")
    if not is_auditable_brand(ctx.brand):
        return _unknown(
            "P3",
            "brand_unknown_cannot_audit",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
        )
    if not product.specs_available:
        return _unknown(
            "P3",
            "specification_table_unavailable",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
            details={"selectors": product.selectors_used},
        )

    specs_text = specs_to_text(product.specs)
    match = brand_or_processor_in_text(ctx.brand or "", specs_text)
    details = {
        "brand": ctx.brand,
        "specs": product.specs,
        **match,
        "selectors": product.selectors_used,
    }
    evidence = specs_text[:500] if specs_text else None
    if match["matched"]:
        evidence = (
            f"brand_name={match['brand_name_match']!r}; "
            f"processor_line={match['processor_line_match']!r}; "
            f"specs={specs_text[:400]!r}"
        )
    return _pass_or_fail(
        "P3",
        ok=bool(match["matched"]),
        evidence_text=evidence,
        source_url=product.source_url,
        screenshot_path=product.screenshot_path,
        details=details,
    )


def evaluate_p4(ctx: AuditContext) -> AuditCheckResult:
    """P4: Brand-led rich media is present."""
    product = ctx.product
    if product is None or not product.available:
        return _unknown("P4", "product_evidence_unavailable")
    if not is_auditable_brand(ctx.brand):
        return _unknown(
            "P4",
            "brand_unknown_cannot_audit",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
        )
    if not product.media_inspected:
        return _unknown(
            "P4",
            "brand_rich_media_not_inspected",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
            details={"selectors": product.selectors_used},
        )

    hit = brand_rich_media_match(ctx.brand or "", product.brand_media_signals)
    details = {
        "brand": ctx.brand,
        "signals": product.brand_media_signals,
        **hit,
        "selectors": product.selectors_used,
    }
    return _pass_or_fail(
        "P4",
        ok=bool(hit.get("matched")),
        evidence_text=hit.get("evidence")
        or ("; ".join(product.brand_media_signals[:5]) if product.brand_media_signals else None),
        source_url=product.source_url,
        screenshot_path=product.screenshot_path,
        details=details,
    )


def evaluate_p5(ctx: AuditContext) -> AuditCheckResult:
    """P5: OEM rich media is present."""
    product = ctx.product
    if product is None or not product.available:
        return _unknown("P5", "product_evidence_unavailable")
    if not is_auditable_oem(ctx.oem):
        return _unknown(
            "P5",
            "oem_unknown_cannot_audit",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
        )
    if not product.media_inspected:
        return _unknown(
            "P5",
            "oem_rich_media_not_inspected",
            source_url=product.source_url,
            screenshot_path=product.screenshot_path,
            details={"selectors": product.selectors_used},
        )

    hit = oem_rich_media_match(ctx.oem or "", product.oem_media_signals)
    details = {
        "oem": ctx.oem,
        "signals": product.oem_media_signals,
        **hit,
        "selectors": product.selectors_used,
    }
    return _pass_or_fail(
        "P5",
        ok=bool(hit.get("matched")),
        evidence_text=hit.get("evidence")
        or ("; ".join(product.oem_media_signals[:5]) if product.oem_media_signals else None),
        source_url=product.source_url,
        screenshot_path=product.screenshot_path,
        details=details,
    )


CHECK_EVALUATORS = {
    "S1": evaluate_s1,
    "S2": evaluate_s2,
    "P1": evaluate_p1,
    "P2": evaluate_p2,
    "P3": evaluate_p3,
    "P4": evaluate_p4,
    "P5": evaluate_p5,
}


def evaluate_all_checks(ctx: AuditContext) -> list[AuditCheckResult]:
    """Run every check independently and return all results."""
    return [CHECK_EVALUATORS[code](ctx) for code in ("S1", "S2", "P1", "P2", "P3", "P4", "P5")]
