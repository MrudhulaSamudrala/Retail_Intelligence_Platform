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

# Reason codes (extensible). Uppercase codes are the collector vocabulary;
# lowercase aliases remain for older rows / tests.
REASON_PDP_BLOCKED = "PDP_BLOCKED"
REASON_LISTING_AVAILABLE = "LISTING_AVAILABLE"
REASON_SPECS_AVAILABLE = "SPECS_AVAILABLE"
REASON_SPECS_NOT_FOUND = "SPECS_NOT_FOUND"
REASON_EVIDENCE_AMBIGUOUS = "EVIDENCE_AMBIGUOUS"
REASON_PAGE_NOT_INSPECTABLE = "PAGE_NOT_INSPECTABLE"
REASON_PARSER_UNCERTAIN = "PARSER_UNCERTAIN"
REASON_ACCOUNT_VERIFICATION = "account_verification"
REASON_CAPTCHA = "captcha"
REASON_BOT_CHALLENGE = "bot_challenge"
REASON_NOT_INSPECTED = "not_inspected"
REASON_SELECTOR_MISSING = "selector_missing"
REASON_PAGINATION_INCOMPLETE = "pagination_incomplete"
REASON_EXTRACTION_FAILED = "extraction_failed"
REASON_LISTING_ONLY = "listing_only"
REASON_OK = "ok"
REASON_API_DISABLED = "API_DISABLED"
REASON_API_UNAVAILABLE = "API_UNAVAILABLE"
REASON_API_RATE_LIMITED = "API_RATE_LIMITED"
REASON_API_ITEM_NOT_FOUND = "API_ITEM_NOT_FOUND"
REASON_API_AUTH_FAILED = "API_AUTH_FAILED"
REASON_API_MALFORMED = "API_MALFORMED"

# Surfaces
SURFACE_LISTING = "listing"
SURFACE_SEARCH = "search"
SURFACE_CATEGORY = "category"
SURFACE_PDP = "pdp"
SURFACE_SPECS = "specifications"
SURFACE_BADGE = "badge"
SURFACE_RICH_MEDIA = "rich_media"
SURFACE_API = "api"

ALL_SURFACES = (
    SURFACE_LISTING,
    SURFACE_SEARCH,
    SURFACE_CATEGORY,
    SURFACE_PDP,
    SURFACE_SPECS,
    SURFACE_BADGE,
    SURFACE_RICH_MEDIA,
    SURFACE_API,
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
    pdp_reason = reason if reason and reason != REASON_OK else REASON_PDP_BLOCKED
    if pdp_reason in {REASON_ACCOUNT_VERIFICATION, REASON_CAPTCHA, REASON_BOT_CHALLENGE}:
        notes = pdp_reason
        pdp_reason = REASON_PDP_BLOCKED
    else:
        notes = reason
    bundle = EvidenceBundle()
    bundle.set_surface(
        SURFACE_LISTING,
        status=COMPLETE,
        reason=REASON_LISTING_AVAILABLE,
        source="listing_card",
    )
    bundle.set_surface(
        SURFACE_PDP,
        status=BLOCKED,
        reason=pdp_reason,
        source="product_page",
        notes=str(notes) if notes else None,
    )
    bundle.set_surface(
        SURFACE_SPECS,
        status=UNKNOWN,
        reason=REASON_PDP_BLOCKED,
        source="product_page",
        notes="title_heuristics_are_not_specs_table_evidence",
    )
    bundle.set_surface(
        SURFACE_BADGE,
        status=UNKNOWN,
        reason=REASON_PDP_BLOCKED,
        source="product_page",
    )
    bundle.set_surface(
        SURFACE_RICH_MEDIA,
        status=UNKNOWN,
        reason=REASON_PDP_BLOCKED,
        source="product_page",
    )
    return bundle


def product_page_evidence(
    *,
    specs_available: bool,
    specs_reason: Optional[str] = None,
    badges_inspected: bool = True,
    media_inspected: bool = True,
) -> EvidenceBundle:
    """Standard bundle when PDP HTML was successfully inspected."""
    bundle = EvidenceBundle()
    bundle.set_surface(
        SURFACE_LISTING,
        status=COMPLETE,
        reason=REASON_LISTING_AVAILABLE,
        source="listing_card",
    )
    bundle.set_surface(
        SURFACE_PDP,
        status=COMPLETE,
        reason=REASON_OK,
        source="product_page",
    )
    if specs_available:
        spec_status = COMPLETE
        spec_reason = specs_reason or REASON_SPECS_AVAILABLE
    else:
        spec_status = UNKNOWN
        spec_reason = specs_reason or REASON_SPECS_NOT_FOUND
    bundle.set_surface(
        SURFACE_SPECS,
        status=spec_status,
        reason=spec_reason,
        source="product_page",
    )
    bundle.set_surface(
        SURFACE_BADGE,
        status=COMPLETE if badges_inspected else UNKNOWN,
        reason=REASON_OK if badges_inspected else REASON_PARSER_UNCERTAIN,
        source="product_page",
        notes="badges_inspected" if badges_inspected else "badges_not_inspected",
    )
    bundle.set_surface(
        SURFACE_RICH_MEDIA,
        status=COMPLETE if media_inspected else UNKNOWN,
        reason=REASON_OK if media_inspected else REASON_PARSER_UNCERTAIN,
        source="product_page",
        notes="media_inspected" if media_inspected else "media_not_inspected",
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
    if "not_inspectable" in lowered or "page_not_inspectable" in lowered:
        return REASON_PAGE_NOT_INSPECTABLE
    return REASON_PDP_BLOCKED
