"""Merge official API observations with existing listing/PDP provenance.

Precedence (documented, not silent overwrite):

Specs / identity (processor, RAM, storage, GPU, display, OS, GTIN, MPN, model, OEM):
    api > product_page > json_ld > embedded_json > network_response > listing_card > title_heuristic

Shopper-visible commercial fields (price, list_price, promo_text, title):
    product_page > listing_card > json_ld > api > embedded_json > title_heuristic

When values disagree, keep the winner and record the alternate + conflict status.
"""

from __future__ import annotations

from typing import Any, Optional

from collector.retailers.mercadolibre.field_evidence import (
    FieldObservation,
    ProvenanceStore,
)

SPEC_FIELDS = frozenset(
    {
        "processor",
        "ram",
        "memory",
        "storage",
        "gpu",
        "display",
        "operating_system",
        "gtin",
        "mpn",
        "model",
        "oem_raw",
        "platform_raw",
        "specs_raw",
    }
)
COMMERCIAL_FIELDS = frozenset({"price", "list_price", "promo_text", "title", "availability"})

_SOURCE_RANK_SPECS = {
    "api": 10,
    "product_page": 20,
    "json_ld": 30,
    "embedded_json": 40,
    "network_response": 50,
    "listing_card": 60,
    "aria": 65,
    "title_heuristic": 70,
}
_SOURCE_RANK_COMMERCIAL = {
    "product_page": 10,
    "listing_card": 20,
    "json_ld": 30,
    "api": 40,
    "embedded_json": 50,
    "network_response": 55,
    "aria": 60,
    "title_heuristic": 70,
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _rank(field: str, source: str) -> int:
    table = _SOURCE_RANK_COMMERCIAL if field in COMMERCIAL_FIELDS else _SOURCE_RANK_SPECS
    return table.get(source, 80)


def merge_stores(base: ProvenanceStore, incoming: ProvenanceStore) -> ProvenanceStore:
    """Merge incoming (typically API) into base without silent overwrite."""
    out = ProvenanceStore(
        fields=dict(base.fields),
        layers_attempted=list(base.layers_attempted),
        conflicts=list(base.conflicts),
    )
    for layer in incoming.layers_attempted:
        out.mark_layer(layer)
    for name, obs in incoming.fields.items():
        existing = out.fields.get(name)
        if existing is None:
            out.fields[name] = obs
            continue
        if name == "specs_raw" and isinstance(existing.value, dict) and isinstance(obs.value, dict):
            merged = dict(existing.value)
            for key, value in obs.value.items():
                merged.setdefault(key, value)
            out.fields[name] = FieldObservation(
                value=merged,
                source=existing.source,
                extraction_method=existing.extraction_method,
                timestamp=existing.timestamp,
                confidence=existing.confidence,
            )
            continue
        if _norm(existing.value) == _norm(obs.value):
            continue
        existing_rank = _rank(name, existing.source)
        new_rank = _rank(name, obs.source)
        if new_rank < existing_rank:
            selected, alternate = obs, existing
        else:
            selected, alternate = existing, obs
        out.fields[name] = selected
        out.conflicts.append(
            {
                "field": name,
                "status": "CONFLICT",
                "selected_value": selected.value,
                "selected_source": selected.source,
                "selected_method": selected.extraction_method,
                "alternate_value": alternate.value,
                "alternate_source": alternate.source,
                "alternate_method": alternate.extraction_method,
            }
        )
    return out


def field_source_map(store: ProvenanceStore) -> dict[str, str]:
    return {name: obs.source for name, obs in store.fields.items()}
