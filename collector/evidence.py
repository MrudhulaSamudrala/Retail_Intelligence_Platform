"""First-class evidence / access status for collection surfaces.

Structured statuses live on ``NormalizedProduct.raw_payload['evidence']`` and
are preserved on product snapshots. Missing/blocked evidence must never become
audit FAIL — checks map BLOCKED/not_inspected → UNKNOWN.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# Overall / per-surface statuses
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
UNKNOWN = "UNKNOWN"
BLOCKED = "BLOCKED"

VALID_STATUSES = frozenset({COMPLETE, PARTIAL, UNKNOWN, BLOCKED})

# Reason codes (extensible)
REASON_PDP_BLOCKED = "pdp_blocked"
REASON_ACCOUNT_VERIFICATION = "account_verification"
REASON_CAPTCHA = "captcha"
REASON_BOT_CHALLENGE = "bot_challenge"
REASON_NOT_INSPECTED = "not_inspected"
REASON_SELECTOR_MISSING = "selector_missing"
REASON_PAGINATION_INCOMPLETE = "pagination_incomplete"
REASON_EXTRACTION_FAILED = "extraction_failed"
REASON_LISTING_ONLY = "listing_only"
REASON_OK = "ok"

# Surfaces
SURFACE_LISTING = "listing"
SURFACE_SEARCH = "search"
SURFACE_CATEGORY = "category"
SURFACE_PDP = "pdp"
SURFACE_SPECS = "specifications"
SURFACE_BADGE = "badge"
SURFACE_RICH_MEDIA = "rich_media"

ALL_SURFACES = (
    SURFACE_LISTING,
    SURFACE_SEARCH,
    SURFACE_CATEGORY,
    SURFACE_PDP,
    SURFACE_SPECS,
    SURFACE_BADGE,
    SURFACE_RICH_MEDIA,
)


@dataclass
class SurfaceEvidence:
    status: str = UNKNOWN
    reason: Optional[str] = None
    source: Optional[str] = None  # listing_card | product_page | search | …
    notes: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"status": self.status}
        if self.reason:
            payload["reason"] = self.reason
        if self.source:
            payload["source"] = self.source
        if self.notes:
            payload["notes"] = self.notes
        return payload


@dataclass
class EvidenceBundle:
    """Per-observation evidence completeness across surfaces."""

    overall_status: str = UNKNOWN
    surfaces: dict[str, SurfaceEvidence] = field(default_factory=dict)

    def set_surface(
        self,
        surface: str,
        *,
        status: str,
        reason: Optional[str] = None,
        source: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid evidence status: {status}")
        self.surfaces[surface] = SurfaceEvidence(
            status=status, reason=reason, source=source, notes=notes
        )
        self.overall_status = derive_overall_status(self.surfaces)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "surfaces": {k: v.to_dict() for k, v in self.surfaces.items()},
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "EvidenceBundle":
        if not isinstance(data, dict):
            return cls()
        surfaces: dict[str, SurfaceEvidence] = {}
        raw_surfaces = data.get("surfaces") or {}
        if isinstance(raw_surfaces, dict):
            for key, val in raw_surfaces.items():
                if isinstance(val, dict):
                    surfaces[str(key)] = SurfaceEvidence(
                        status=str(val.get("status") or UNKNOWN),
                        reason=val.get("reason"),
                        source=val.get("source"),
                        notes=val.get("notes"),
                    )
        overall = str(data.get("overall_status") or derive_overall_status(surfaces))
        return cls(overall_status=overall, surfaces=surfaces)

    def pdp_inspected(self) -> bool:
        pdp = self.surfaces.get(SURFACE_PDP)
        return bool(pdp and pdp.status == COMPLETE)

    def pdp_blocked(self) -> bool:
        pdp = self.surfaces.get(SURFACE_PDP)
        return bool(pdp and pdp.status == BLOCKED)


def derive_overall_status(surfaces: dict[str, SurfaceEvidence]) -> str:
    if not surfaces:
        return UNKNOWN
    statuses = {s.status for s in surfaces.values()}
    if BLOCKED in statuses and COMPLETE in statuses:
        return PARTIAL
    if statuses == {COMPLETE}:
        return COMPLETE
    if BLOCKED in statuses and COMPLETE not in statuses:
        # Listing-only observation still has some evidence.
        if any(s.status == COMPLETE for s in surfaces.values()):
            return PARTIAL
        return BLOCKED
    if PARTIAL in statuses or (COMPLETE in statuses and UNKNOWN in statuses):
        return PARTIAL
    if COMPLETE in statuses:
        return COMPLETE
    return UNKNOWN


def listing_only_evidence(*, reason: str = REASON_ACCOUNT_VERIFICATION) -> EvidenceBundle:
    """Standard bundle when PDP is blocked and listing card was used."""
    bundle = EvidenceBundle()
    bundle.set_surface(
        SURFACE_LISTING,
        status=COMPLETE,
        reason=REASON_OK,
        source="listing_card",
    )
    bundle.set_surface(
        SURFACE_PDP,
        status=BLOCKED,
        reason=reason if reason != REASON_OK else REASON_PDP_BLOCKED,
        source="product_page",
    )
    bundle.set_surface(
        SURFACE_SPECS,
        status=UNKNOWN,
        reason=REASON_NOT_INSPECTED,
        source="product_page",
        notes="title_heuristics_are_not_specs_table_evidence",
    )
    bundle.set_surface(
        SURFACE_BADGE,
        status=UNKNOWN,
        reason=REASON_NOT_INSPECTED,
        source="product_page",
    )
    bundle.set_surface(
        SURFACE_RICH_MEDIA,
        status=UNKNOWN,
        reason=REASON_NOT_INSPECTED,
        source="product_page",
    )
    return bundle


def product_page_evidence(*, specs_available: bool) -> EvidenceBundle:
    """Standard bundle when PDP HTML was successfully inspected."""
    bundle = EvidenceBundle()
    bundle.set_surface(
        SURFACE_LISTING,
        status=COMPLETE,
        reason=REASON_OK,
        source="listing_card",
    )
    bundle.set_surface(
        SURFACE_PDP,
        status=COMPLETE,
        reason=REASON_OK,
        source="product_page",
    )
    bundle.set_surface(
        SURFACE_SPECS,
        status=COMPLETE if specs_available else UNKNOWN,
        reason=REASON_OK if specs_available else REASON_SELECTOR_MISSING,
        source="product_page",
    )
    bundle.set_surface(
        SURFACE_BADGE,
        status=COMPLETE,
        reason=REASON_OK,
        source="product_page",
        notes="badges_inspected",
    )
    bundle.set_surface(
        SURFACE_RICH_MEDIA,
        status=COMPLETE,
        reason=REASON_OK,
        source="product_page",
        notes="media_inspected",
    )
    return bundle


def map_block_reason(blocked: str) -> str:
    lowered = (blocked or "").lower()
    if "account" in lowered or "verification" in lowered:
        return REASON_ACCOUNT_VERIFICATION
    if "captcha" in lowered:
        return REASON_CAPTCHA
    if "bot" in lowered or "challenge" in lowered:
        return REASON_BOT_CHALLENGE
    return REASON_PDP_BLOCKED
