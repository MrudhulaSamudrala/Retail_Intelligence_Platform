"""Overall Brand Compliance Score analytics.

Computes Notebook and Desktop segment scores from S1–P5 audit results, then
applies the brief's authoritative weighting:

    overall = notebook × 0.85 + desktop × 0.15

Individual S1–P5 combination is configurable (see docs/clarifications.md C1).
Workstation, Tablet, CPU, and GPU are excluded from the overall weighting unless
explicitly enabled in config.
"""

from analytics.compliance.config import (
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
from analytics.compliance.scoring import (
    compute_brand_scores,
    compute_compliance_score,
    compute_country_scores,
    compute_retailer_scores,
    compute_segment_score,
)

__all__ = [
    "STRATEGY_EQUAL",
    "STRATEGY_CONFIGURED",
    "STRATEGY_POOLED",
    "AuditScoreRow",
    "CheckScore",
    "ComplianceScore",
    "ComplianceScoreConfig",
    "CoverageStats",
    "SegmentScore",
    "compute_brand_scores",
    "compute_compliance_score",
    "compute_country_scores",
    "compute_retailer_scores",
    "compute_segment_score",
    "load_compliance_score_config",
]
