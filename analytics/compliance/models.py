"""Data structures for Brand Compliance Score outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class AuditScoreRow:
    """One audit check observation used as scoring input.

    Mirrors the fields needed from ``retailer_audits`` without requiring a DB
    session — callers may map ORM rows into this shape.
    """

    brand: Optional[str]
    retailer_code: str
    country_code: str
    product_type: Optional[str]
    check_code: str
    result: str  # PASS | FAIL | UNKNOWN
    product_id: Optional[int] = None


@dataclass
class CoverageStats:
    """PASS / FAIL / UNKNOWN counts for a scoring slice."""

    pass_count: int = 0
    fail_count: int = 0
    unknown_count: int = 0

    @property
    def scored_count(self) -> int:
        return self.pass_count + self.fail_count

    @property
    def total_count(self) -> int:
        return self.pass_count + self.fail_count + self.unknown_count

    @property
    def coverage_rate(self) -> Optional[float]:
        """Fraction of results that are scored (not UNKNOWN)."""
        total = self.total_count
        if total == 0:
            return None
        return self.scored_count / total

    @property
    def pass_rate(self) -> Optional[float]:
        """PASS / (PASS + FAIL); UNKNOWN excluded from denominator."""
        scored = self.scored_count
        if scored == 0:
            return None
        return self.pass_count / scored


@dataclass
class CheckScore:
    """Pass rate for a single audit check code within a segment/scope."""

    check_code: str
    score: Optional[float]
    coverage: CoverageStats = field(default_factory=CoverageStats)


@dataclass
class SegmentScore:
    """Compliance score for one product-type segment (e.g. notebook)."""

    product_type: str
    score: Optional[float]
    check_scores: dict[str, CheckScore] = field(default_factory=dict)
    coverage: CoverageStats = field(default_factory=CoverageStats)
    included_in_overall_weighting: bool = False
    segment_weight: Optional[float] = None


@dataclass
class ComplianceScore:
    """Notebook, Desktop, and final weighted Brand Compliance Score for a scope."""

    brand: Optional[str] = None
    retailer_code: Optional[str] = None
    country_code: Optional[str] = None
    notebook: Optional[SegmentScore] = None
    desktop: Optional[SegmentScore] = None
    other_segments: dict[str, SegmentScore] = field(default_factory=dict)
    overall_score: Optional[float] = None
    notebook_weight: float = 0.85
    desktop_weight: float = 0.15
    check_aggregation_strategy: Optional[str] = None
    coverage: CoverageStats = field(default_factory=CoverageStats)
