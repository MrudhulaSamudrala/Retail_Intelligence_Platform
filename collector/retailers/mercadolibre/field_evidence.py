"""Per-field provenance for Mercado Libre extraction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# Extraction methods (controlled vocabulary)
METHOD_DOM_TEXT = "DOM_text"
METHOD_DOM_ARIA = "DOM_aria"
METHOD_DOM_ALT = "DOM_alt"
METHOD_DOM_TITLE = "DOM_title"
METHOD_JSON_LD = "json_ld"
METHOD_EMBEDDED_STATE = "embedded_state"
METHOD_NETWORK_JSON = "network_json"
METHOD_LISTING_CARD = "listing_card"
METHOD_TITLE_REGEX = "title_regex"

# Sources
SOURCE_LISTING_CARD = "listing_card"
SOURCE_PRODUCT_PAGE = "product_page"
SOURCE_JSON_LD = "json_ld"
SOURCE_EMBEDDED_JSON = "embedded_json"
SOURCE_NETWORK = "network_response"
SOURCE_ARIA = "aria"
SOURCE_TITLE_HEURISTIC = "title_heuristic"

LAYER_RANK = {
    METHOD_DOM_TEXT: 10,
    METHOD_DOM_ARIA: 20,
    METHOD_DOM_ALT: 21,
    METHOD_DOM_TITLE: 22,
    METHOD_JSON_LD: 30,
    METHOD_EMBEDDED_STATE: 40,
    METHOD_NETWORK_JSON: 50,
    METHOD_LISTING_CARD: 60,
    METHOD_TITLE_REGEX: 70,
}

METHOD_CONFIDENCE = {
    METHOD_DOM_TEXT: 0.92,
    METHOD_DOM_ARIA: 0.78,
    METHOD_DOM_ALT: 0.75,
    METHOD_DOM_TITLE: 0.75,
    METHOD_JSON_LD: 0.88,
    METHOD_EMBEDDED_STATE: 0.86,
    METHOD_NETWORK_JSON: 0.82,
    METHOD_LISTING_CARD: 0.72,
    METHOD_TITLE_REGEX: 0.55,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class FieldObservation:
    value: Any
    source: str
    extraction_method: str
    timestamp: str
    confidence: Optional[float] = None
    raw: Optional[str] = None
    currency: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {k: v for k, v in payload.items() if v is not None}


def observation(
    value: Any,
    *,
    source: str,
    extraction_method: str,
    raw: Optional[str] = None,
    currency: Optional[str] = None,
    timestamp: Optional[str] = None,
    confidence: Optional[float] = None,
) -> FieldObservation:
    conf = confidence
    if conf is None:
        conf = METHOD_CONFIDENCE.get(extraction_method)
    return FieldObservation(
        value=value,
        source=source,
        extraction_method=extraction_method,
        timestamp=timestamp or utc_now_iso(),
        confidence=conf,
        raw=raw,
        currency=currency,
    )


@dataclass
class ProvenanceStore:
    """Keep the best (lowest layer rank) observation per field; record attempts."""

    fields: dict[str, FieldObservation] = field(default_factory=dict)
    layers_attempted: list[str] = field(default_factory=list)

    def mark_layer(self, name: str) -> None:
        if name not in self.layers_attempted:
            self.layers_attempted.append(name)

    def set_if_empty(self, name: str, obs: Optional[FieldObservation]) -> bool:
        if obs is None or obs.value in (None, "", [], {}):
            return False
        existing = self.fields.get(name)
        if existing is None:
            self.fields[name] = obs
            return True
        old_rank = LAYER_RANK.get(existing.extraction_method, 99)
        new_rank = LAYER_RANK.get(obs.extraction_method, 99)
        if new_rank < old_rank:
            self.fields[name] = obs
            return True
        return False

    def get_value(self, name: str) -> Any:
        obs = self.fields.get(name)
        return obs.value if obs else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layers_attempted": list(self.layers_attempted),
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
        }

    def unknown_fields(self, required: list[str]) -> dict[str, str]:
        """Fields still missing after all layers, with why."""
        missing: dict[str, str] = {}
        attempted = ",".join(self.layers_attempted) or "none"
        for name in required:
            if self.get_value(name) in (None, "", [], {}):
                missing[name] = (
                    f"PARSER_UNCERTAIN:all_layers_tried_no_reliable_value:"
                    f"{attempted}"
                )
        return missing
