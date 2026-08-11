"""Retailer audit check evaluation (S1, S2, P1–P5).

Brief definitions:
- S1: Listing title includes brand name and/or brand-specific processor line
- S2: Brand badge present on listing tile
- P1: Product title includes brand name, processor line or generation
- P2: Brand badge present on product page
- P3: Brand or processor line in specification table
- P4: Brand-led rich media present
- P5: OEM rich media present

Each check returns PASS / FAIL / UNKNOWN independently with evidence.
Overall compliance scoring is intentionally out of scope for this module.
"""

from collector.audit.checks import (
    evaluate_all_checks,
    evaluate_p1,
    evaluate_p2,
    evaluate_p3,
    evaluate_p4,
    evaluate_p5,
    evaluate_s1,
    evaluate_s2,
)
from collector.audit.engine import (
    build_context_for_product,
    build_product_evidence_from_normalized,
    evaluate_and_persist,
    persist_audit_results,
    run_audits,
)
from collector.audit.models import (
    FAIL,
    PASS,
    UNKNOWN,
    AuditCheckResult,
    AuditContext,
    ListingEvidence,
    ProductEvidence,
)

__all__ = [
    "PASS",
    "FAIL",
    "UNKNOWN",
    "AuditCheckResult",
    "AuditContext",
    "ListingEvidence",
    "ProductEvidence",
    "evaluate_s1",
    "evaluate_s2",
    "evaluate_p1",
    "evaluate_p2",
    "evaluate_p3",
    "evaluate_p4",
    "evaluate_p5",
    "evaluate_all_checks",
    "run_audits",
    "persist_audit_results",
    "evaluate_and_persist",
    "build_context_for_product",
    "build_product_evidence_from_normalized",
]
