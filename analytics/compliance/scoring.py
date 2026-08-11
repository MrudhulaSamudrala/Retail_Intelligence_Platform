"""Pure Brand Compliance Score calculation (no database I/O)."""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from analytics.compliance.config import (
    CHECK_CODES,
    STRATEGY_CONFIGURED,
    STRATEGY_EQUAL,
    STRATEGY_POOLED,
    ComplianceScoreConfig,
    load_compliance_score_config,
)
from analytics.compliance.models import (
    AuditScoreRow,
    CheckScore,
    ComplianceScore,
    CoverageStats,
    SegmentScore,
)

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


def _accumulate(coverage: CoverageStats, result: str) -> None:
    if result == PASS:
        coverage.pass_count += 1
    elif result == FAIL:
        coverage.fail_count += 1
    elif result == UNKNOWN:
        coverage.unknown_count += 1


def _merge_coverage(parts: Iterable[CoverageStats]) -> CoverageStats:
    merged = CoverageStats()
    for part in parts:
        merged.pass_count += part.pass_count
        merged.fail_count += part.fail_count
        merged.unknown_count += part.unknown_count
    return merged


def _filter_rows(
    rows: Sequence[AuditScoreRow],
    *,
    brand: Optional[str] = None,
    retailer_code: Optional[str] = None,
    country_code: Optional[str] = None,
    product_type: Optional[str] = None,
) -> list[AuditScoreRow]:
    out: list[AuditScoreRow] = []
    for row in rows:
        if brand is not None and row.brand != brand:
            continue
        if retailer_code is not None and row.retailer_code != retailer_code:
            continue
        if country_code is not None and row.country_code != country_code:
            continue
        if product_type is not None and row.product_type != product_type:
            continue
        out.append(row)
    return out


def _check_coverages(rows: Sequence[AuditScoreRow]) -> dict[str, CoverageStats]:
    by_check: dict[str, CoverageStats] = {code: CoverageStats() for code in CHECK_CODES}
    for row in rows:
        if row.check_code not in by_check:
            continue
        _accumulate(by_check[row.check_code], row.result)
    return by_check


def _combine_check_scores(
    check_scores: dict[str, CheckScore],
    *,
    strategy: str,
    check_weights: dict[str, float],
    segment_coverage: CoverageStats,
) -> Optional[float]:
    """Combine per-check pass rates according to the configured strategy."""
    if strategy == STRATEGY_POOLED:
        return segment_coverage.pass_rate

    scored: list[tuple[str, float]] = [
        (code, cs.score)
        for code, cs in check_scores.items()
        if cs.score is not None
    ]
    if not scored:
        return None

    if strategy == STRATEGY_EQUAL:
        return sum(score for _, score in scored) / len(scored)

    if strategy == STRATEGY_CONFIGURED:
        weight_sum = 0.0
        weighted = 0.0
        for code, score in scored:
            weight = float(check_weights.get(code, 0.0))
            if weight <= 0:
                continue
            weighted += score * weight
            weight_sum += weight
        if weight_sum <= 0:
            return None
        return weighted / weight_sum

    raise ValueError(f"Unsupported check aggregation strategy: {strategy!r}")


def compute_segment_score(
    rows: Sequence[AuditScoreRow],
    product_type: str,
    *,
    config: Optional[ComplianceScoreConfig] = None,
) -> SegmentScore:
    """Compute a single product-type segment score from audit rows."""
    cfg = config or load_compliance_score_config()
    segment_rows = [r for r in rows if r.product_type == product_type]
    by_check = _check_coverages(segment_rows)

    check_scores: dict[str, CheckScore] = {}
    for code in CHECK_CODES:
        coverage = by_check[code]
        check_scores[code] = CheckScore(
            check_code=code,
            score=coverage.pass_rate,
            coverage=coverage,
        )

    coverage = _merge_coverage(cs.coverage for cs in check_scores.values())
    score = _combine_check_scores(
        check_scores,
        strategy=cfg.strategy,
        check_weights=cfg.check_weights,
        segment_coverage=coverage,
    )

    included = product_type in cfg.weighted_product_types
    return SegmentScore(
        product_type=product_type,
        score=score,
        check_scores=check_scores,
        coverage=coverage,
        included_in_overall_weighting=included,
        segment_weight=cfg.weight_for_segment(product_type) if included else None,
    )


def _final_weighted_score(
    notebook: SegmentScore,
    desktop: SegmentScore,
    *,
    notebook_weight: float,
    desktop_weight: float,
) -> Optional[float]:
    """Brief formula. Requires both segments; does not renormalize (clarifications C2)."""
    if notebook.score is None or desktop.score is None:
        return None
    return (notebook.score * notebook_weight) + (desktop.score * desktop_weight)


def compute_compliance_score(
    rows: Sequence[AuditScoreRow],
    *,
    brand: Optional[str] = None,
    retailer_code: Optional[str] = None,
    country_code: Optional[str] = None,
    config: Optional[ComplianceScoreConfig] = None,
) -> ComplianceScore:
    """Compute notebook, desktop, and final weighted scores for a scope.

    Filters optional brand / retailer / country. Overall weighting uses only
    product types marked ``included_in_compliance_weighting`` (Notebook + Desktop
    by default). Other audited types are reported under ``other_segments`` and
    never enter the 85/15 formula.
    """
    cfg = config or load_compliance_score_config()
    scoped = _filter_rows(
        rows,
        brand=brand,
        retailer_code=retailer_code,
        country_code=country_code,
    )

    notebook_w = float(cfg.segment_weights["notebook"])
    desktop_w = float(cfg.segment_weights["desktop"])

    notebook = compute_segment_score(scoped, "notebook", config=cfg)
    desktop = compute_segment_score(scoped, "desktop", config=cfg)

    other_segments: dict[str, SegmentScore] = {}
    seen_types = {
        r.product_type
        for r in scoped
        if r.product_type and r.product_type not in {"notebook", "desktop"}
    }
    for product_type in sorted(seen_types):
        other_segments[product_type] = compute_segment_score(
            scoped, product_type, config=cfg
        )

    overall = _final_weighted_score(
        notebook,
        desktop,
        notebook_weight=notebook_w,
        desktop_weight=desktop_w,
    )

    # Coverage for overall formula inputs only (notebook + desktop).
    overall_coverage = _merge_coverage([notebook.coverage, desktop.coverage])

    return ComplianceScore(
        brand=brand,
        retailer_code=retailer_code,
        country_code=country_code,
        notebook=notebook,
        desktop=desktop,
        other_segments=other_segments,
        overall_score=overall,
        notebook_weight=notebook_w,
        desktop_weight=desktop_w,
        check_aggregation_strategy=cfg.strategy,
        coverage=overall_coverage,
    )


def compute_brand_scores(
    rows: Sequence[AuditScoreRow],
    *,
    retailer_code: Optional[str] = None,
    country_code: Optional[str] = None,
    config: Optional[ComplianceScoreConfig] = None,
) -> dict[str, ComplianceScore]:
    """Brand-level scores (optionally scoped to a retailer and/or country)."""
    cfg = config or load_compliance_score_config()
    scoped = _filter_rows(rows, retailer_code=retailer_code, country_code=country_code)
    brands = sorted({r.brand for r in scoped if r.brand})
    return {
        brand: compute_compliance_score(
            scoped,
            brand=brand,
            retailer_code=retailer_code,
            country_code=country_code,
            config=cfg,
        )
        for brand in brands
    }


def compute_retailer_scores(
    rows: Sequence[AuditScoreRow],
    *,
    brand: Optional[str] = None,
    country_code: Optional[str] = None,
    config: Optional[ComplianceScoreConfig] = None,
) -> dict[str, ComplianceScore]:
    """Retailer-level scores (optionally scoped to a brand and/or country)."""
    cfg = config or load_compliance_score_config()
    scoped = _filter_rows(rows, brand=brand, country_code=country_code)
    retailers = sorted({r.retailer_code for r in scoped})
    return {
        retailer: compute_compliance_score(
            scoped,
            brand=brand,
            retailer_code=retailer,
            country_code=country_code,
            config=cfg,
        )
        for retailer in retailers
    }


def compute_country_scores(
    rows: Sequence[AuditScoreRow],
    *,
    brand: Optional[str] = None,
    retailer_code: Optional[str] = None,
    config: Optional[ComplianceScoreConfig] = None,
) -> dict[str, ComplianceScore]:
    """Country-level scores (optionally scoped to a brand and/or retailer)."""
    cfg = config or load_compliance_score_config()
    scoped = _filter_rows(rows, brand=brand, retailer_code=retailer_code)
    countries = sorted({r.country_code for r in scoped})
    return {
        country: compute_compliance_score(
            scoped,
            brand=brand,
            retailer_code=retailer_code,
            country_code=country,
            config=cfg,
        )
        for country in countries
    }

