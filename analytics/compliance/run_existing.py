"""Calculate Overall Brand Compliance Score from existing retailer_audits rows.

Loads S1–P5 results already stored in PostgreSQL and scores them with the
configured strategy from ``config/compliance.yaml``. Does not collect new data
or change scoring methodology.

Usage (from repo root):

    python -m analytics.compliance.run_existing
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import func, select, text

from analytics.compliance import (
    AuditScoreRow,
    ComplianceScore,
    compute_brand_scores,
    compute_compliance_score,
    compute_country_scores,
    compute_retailer_scores,
    load_compliance_score_config,
)
from analytics.compliance.config import CHECK_CODES
from analytics.compliance.queries import load_audit_rows
from database.connection import session_scope
from database.models import Product, RetailerAudit


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def collect_validation_stats(session, rows: list[AuditScoreRow]) -> dict:
    """Pre-score inventory: products, audit result counts, scoring eligibility."""
    product_count = session.scalar(select(func.count()).select_from(Product)) or 0
    audit_count = len(rows)

    result_counts = {"PASS": 0, "FAIL": 0, "UNKNOWN": 0}
    check_codes = set(CHECK_CODES)
    for row in rows:
        if row.result in result_counts:
            result_counts[row.result] += 1

    # Distinct products with any audit / any scored (PASS|FAIL) audit
    products_with_audit = session.execute(
        text(
            """
            SELECT COUNT(DISTINCT product_id)
            FROM retailer_audits
            WHERE product_id IS NOT NULL
            """
        )
    ).scalar_one()

    products_with_scored = session.execute(
        text(
            """
            SELECT COUNT(DISTINCT product_id)
            FROM retailer_audits
            WHERE product_id IS NOT NULL
              AND result IN ('PASS', 'FAIL')
            """
        )
    ).scalar_one()

    # Products with at least one scored notebook/desktop audit (eligible for overall)
    products_weighted_scored = session.execute(
        text(
            """
            SELECT COUNT(DISTINCT product_id)
            FROM retailer_audits
            WHERE product_id IS NOT NULL
              AND result IN ('PASS', 'FAIL')
              AND product_type IN ('notebook', 'desktop')
            """
        )
    ).scalar_one()

    products_only_other_or_unscored = session.execute(
        text(
            """
            SELECT COUNT(*) FROM products p
            WHERE NOT EXISTS (
                SELECT 1 FROM retailer_audits a
                WHERE a.product_id = p.id
                  AND a.result IN ('PASS', 'FAIL')
                  AND a.product_type IN ('notebook', 'desktop')
            )
            """
        )
    ).scalar_one()

    by_type_scored = session.execute(
        text(
            """
            SELECT product_type,
                   COUNT(*) FILTER (WHERE result = 'PASS') AS pass_n,
                   COUNT(*) FILTER (WHERE result = 'FAIL') AS fail_n,
                   COUNT(*) FILTER (WHERE result = 'UNKNOWN') AS unknown_n,
                   COUNT(DISTINCT product_id) FILTER (
                       WHERE result IN ('PASS', 'FAIL')
                   ) AS scored_products
            FROM retailer_audits
            GROUP BY product_type
            ORDER BY product_type NULLS LAST
            """
        )
    ).all()

    by_check = session.execute(
        text(
            """
            SELECT check_code, result, COUNT(*) AS n
            FROM retailer_audits
            WHERE check_code = ANY(:codes)
            GROUP BY check_code, result
            ORDER BY check_code, result
            """
        ),
        {"codes": list(check_codes)},
    ).all()

    exclusion_reasons: dict[str, int] = defaultdict(int)
    # Product-level exclusion breakdown
    product_rows = session.execute(
        text(
            """
            SELECT p.id,
                   p.product_type,
                   EXISTS (
                       SELECT 1 FROM retailer_audits a WHERE a.product_id = p.id
                   ) AS has_audit,
                   EXISTS (
                       SELECT 1 FROM retailer_audits a
                       WHERE a.product_id = p.id AND a.result IN ('PASS', 'FAIL')
                   ) AS has_scored,
                   EXISTS (
                       SELECT 1 FROM retailer_audits a
                       WHERE a.product_id = p.id
                         AND a.result IN ('PASS', 'FAIL')
                         AND a.product_type IN ('notebook', 'desktop')
                   ) AS has_weighted_scored
            FROM products p
            """
        )
    ).all()

    for _pid, ptype, has_audit, has_scored, has_weighted_scored in product_rows:
        if has_weighted_scored:
            continue
        if not has_audit:
            exclusion_reasons["no_audit_records"] += 1
        elif not has_scored:
            exclusion_reasons["audits_only_UNKNOWN"] += 1
        elif ptype in {"workstation", "tablet", "cpu", "gpu"}:
            exclusion_reasons[f"product_type_excluded_from_85_15:{ptype}"] += 1
        else:
            exclusion_reasons["no_scored_notebook_or_desktop_audits"] += 1

    return {
        "product_count": int(product_count),
        "audit_count": int(audit_count),
        "result_counts": result_counts,
        "products_with_audit": int(products_with_audit),
        "products_with_scored": int(products_with_scored),
        "products_weighted_scored": int(products_weighted_scored),
        "products_excluded_from_overall": int(products_only_other_or_unscored),
        "exclusion_reasons": dict(exclusion_reasons),
        "by_type_scored": by_type_scored,
        "by_check": by_check,
    }


def format_report(
    *,
    strategy: str,
    stats: dict,
    overall: ComplianceScore,
    by_brand: dict[str, ComplianceScore],
    by_retailer: dict[str, ComplianceScore],
    by_country: dict[str, ComplianceScore],
) -> str:
    rc = stats["result_counts"]
    lines: list[str] = []
    lines.append("CURRENT COMPLIANCE SCORE")
    lines.append("========================")
    lines.append("")
    lines.append("Scoring strategy:")
    lines.append(strategy)
    if strategy == "equal_check_weights":
        lines.append(
            "(interim default - Bridge AI has not specified individual S1-P5 weights;"
            " see docs/clarifications.md C1)"
        )
    lines.append("")
    lines.append("VALIDATION (before scoring)")
    lines.append("---------------------------")
    lines.append(f"Products (total):                         {stats['product_count']}")
    lines.append(f"Audit observations (total):               {stats['audit_count']}")
    lines.append(f"PASS:                                     {rc['PASS']}")
    lines.append(f"FAIL:                                     {rc['FAIL']}")
    lines.append(f"UNKNOWN:                                  {rc['UNKNOWN']}")
    lines.append(
        f"Products with any audit:                  {stats['products_with_audit']}"
    )
    lines.append(
        f"Products with scored data (PASS/FAIL):    {stats['products_with_scored']}"
    )
    lines.append(
        f"Products with scored notebook/desktop:    {stats['products_weighted_scored']}"
    )
    lines.append(
        f"Products excluded from 85/15 overall:     {stats['products_excluded_from_overall']}"
    )
    if stats["exclusion_reasons"]:
        lines.append("Exclusion reasons (product counts):")
        for reason, count in sorted(stats["exclusion_reasons"].items()):
            lines.append(f"  - {reason}: {count}")
    lines.append("")
    lines.append("Audit counts by product_type:")
    for row in stats["by_type_scored"]:
        ptype = row[0] or "NULL"
        lines.append(
            f"  {ptype}: PASS={row[1]} FAIL={row[2]} UNKNOWN={row[3]} "
            f"scored_products={row[4]}"
        )
    lines.append("")
    lines.append("Products:")
    lines.append(str(stats["product_count"]))
    lines.append("")
    lines.append("Audit observations:")
    lines.append(f"PASS    = {rc['PASS']}")
    lines.append(f"FAIL    = {rc['FAIL']}")
    lines.append(f"UNKNOWN = {rc['UNKNOWN']}")
    lines.append("")

    nb = overall.notebook.score if overall.notebook else None
    dt = overall.desktop.score if overall.desktop else None
    lines.append(f"Notebook Score:    {_pct(nb)}")
    lines.append(f"Desktop Score:     {_pct(dt)}")
    lines.append("")
    lines.append("Overall Score:")
    lines.append("(Notebook x 0.85) + (Desktop x 0.15)")
    if overall.overall_score is None:
        lines.append("= n/a  (both Notebook and Desktop scored segments required)")
    else:
        lines.append(f"= {_pct(overall.overall_score)}")
    lines.append("")

    lines.append("Brand Scores:")
    if not by_brand:
        lines.append("(none)")
    else:
        for brand, score in by_brand.items():
            lines.append(f"{brand} = {_pct(score.overall_score)}")
            if score.overall_score is None:
                lines.append(
                    f"  notebook={_pct(score.notebook.score if score.notebook else None)} "
                    f"desktop={_pct(score.desktop.score if score.desktop else None)}"
                )
    lines.append("")

    lines.append("Retailer Scores:")
    if not by_retailer:
        lines.append("(none)")
    else:
        for retailer, score in by_retailer.items():
            lines.append(f"{retailer} = {_pct(score.overall_score)}")
            if score.overall_score is None:
                lines.append(
                    f"  notebook={_pct(score.notebook.score if score.notebook else None)} "
                    f"desktop={_pct(score.desktop.score if score.desktop else None)}"
                )
    lines.append("")

    lines.append("Country Scores:")
    if not by_country:
        lines.append("(none)")
    else:
        for country, score in by_country.items():
            lines.append(f"{country} = {_pct(score.overall_score)}")
            if score.overall_score is None:
                lines.append(
                    f"  notebook={_pct(score.notebook.score if score.notebook else None)} "
                    f"desktop={_pct(score.desktop.score if score.desktop else None)}"
                )
    lines.append("")

    lines.append("Other Segments:")
    other_labels = {
        "workstation": "Workstation",
        "tablet": "Tablet",
        "cpu": "CPU",
        "gpu": "GPU",
    }
    other = overall.other_segments or {}
    for key, label in other_labels.items():
        if key in other:
            lines.append(f"{label:12} = {_pct(other[key].score)}")
        else:
            lines.append(f"{label:12} = n/a")
    for key, seg in sorted(other.items()):
        if key not in other_labels:
            lines.append(f"{key:12} = {_pct(seg.score)}")
    lines.append("")
    lines.append("Note:")
    lines.append("Other segments are NOT included in the 85/15 overall score.")
    lines.append("UNKNOWN results are excluded from score denominators (not treated as FAIL).")
    return "\n".join(lines)


def main() -> int:
    load_dotenv()
    cfg = load_compliance_score_config()

    with session_scope() as session:
        # Confirm source table before scoring
        audit_orm_count = session.scalar(
            select(func.count()).select_from(RetailerAudit)
        )
        rows = load_audit_rows(session)
        assert len(rows) == audit_orm_count
        stats = collect_validation_stats(session, rows)

        overall = compute_compliance_score(rows, config=cfg)
        by_brand = compute_brand_scores(rows, config=cfg)
        by_retailer = compute_retailer_scores(rows, config=cfg)
        by_country = compute_country_scores(rows, config=cfg)

        report = format_report(
            strategy=cfg.strategy,
            stats=stats,
            overall=overall,
            by_brand=by_brand,
            by_retailer=by_retailer,
            by_country=by_country,
        )
        print(report)
        print()
        print("REPRODUCE")
        print("---------")
        print("Command:")
        print("  python -m analytics.compliance.run_existing")
        print()
        print("API:")
        print("  analytics.compliance.queries.load_audit_rows(session)")
        print("  -> latest eligible (product_id, check_code) in latest audit batch")
        print("  -> compute_compliance_score / compute_brand_scores /")
        print("     compute_retailer_scores / compute_country_scores")
        print("  Note: Newegg stratified collection currently lacks persisted")
        print("  S1–P5 audit rows; this command does not fabricate them.")
        print(f"  config strategy: {cfg.strategy}")
        print(f"  segment_weights: {cfg.segment_weights}")
        print()
        print("SQL (inventory):")
        print("  SELECT COUNT(*) FROM products;")
        print("  SELECT result, COUNT(*) FROM retailer_audits GROUP BY result;")
        print(
            "  SELECT product_type, check_code, result, COUNT(*) "
            "FROM retailer_audits GROUP BY 1,2,3;"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
