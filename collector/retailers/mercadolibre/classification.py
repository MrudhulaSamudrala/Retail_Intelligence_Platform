"""Two-stage Mercado Libre product classification.

Stage 1 — hard-negative / structural rejection (TV, power bank, bike, …)
Stage 2 — positive computing relevance from title/category/specs

Discovery URL/slugs never force a product type. Weak aliases such as a bare
``computador`` substring must not override hard negatives.

Returns controlled statuses: VALID | UNKNOWN | EXCLUDED.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from collector.config_loader import load_product_types
from collector.normalize import UNKNOWN, _is_discovery_slug, _match_type_in_blob

VALID = "VALID"
EXCLUDED = "EXCLUDED"
# Re-export UNKNOWN for callers
__all__ = [
    "VALID",
    "UNKNOWN",
    "EXCLUDED",
    "ClassificationResult",
    "classify_mercadolibre_product",
    "is_collection_eligible",
]

SUPPORTED_PRODUCT_TYPES = frozenset(
    {"notebook", "desktop", "workstation", "tablet", "cpu", "gpu"}
)

_DEFAULT_HARD_NEGATIVES = (
    "smart tv",
    "smart-tv",
    "televisão",
    "televisao",
    "power bank",
    "powerbank",
    "carregador portátil",
    "carregador portatil",
    "suplemento",
    "vitamina",
    "whey",
    "creatina",
    "omega plus",
    "dark lab",
    "bicicleta",
    "spinning",
    "esteira",
    "elíptico",
    "eliptico",
    "smartphone",
    "celular",
    "iphone",
    "geladeira",
    "fogão",
    "fogao",
    "máquina de lavar",
    "maquina de lavar",
    "air fryer",
    "fone de ouvido",
    "headset gamer",  # accessory — not a computing system product for ML target
)

_GAMING_TITLE_RE = re.compile(
    r"\b(gamer|gaming|rog|tuf|legion|omen|predator|nitro|alienware|"
    r"geforce|rtx|gtx|radeon\s*rx)\b",
    re.I,
)


@dataclass
class ClassificationResult:
    status: str  # VALID | UNKNOWN | EXCLUDED
    product_type: str = UNKNOWN
    confidence: float = 0.0
    gaming: bool = False
    hard_negative: bool = False
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hard_negative_hits(title: Optional[str]) -> list[str]:
    if not title:
        return []
    title_l = title.lower()
    cfg = load_product_types()
    signals = list(cfg.get("irrelevant_title_signals") or [])
    # Ensure smartphone / appliance coverage even if YAML lags.
    for extra in _DEFAULT_HARD_NEGATIVES:
        if extra not in signals:
            signals.append(extra)
    hits: list[str] = []
    for signal in signals:
        token = str(signal).lower().strip()
        if token and token in title_l:
            hits.append(token)
    if re.search(r"\btvs?\b", title_l) and "tv" not in hits:
        hits.append("tv")
    if re.search(r"\b(smart)?phones?\b", title_l) and "smartphone" not in hits:
        # Avoid "telephone" noise; require phone/smartphone word.
        if re.search(r"\b(smartphone|celular|iphone|phones?)\b", title_l):
            hits.append("smartphone")
    return hits


def _positive_type(
    *,
    title: Optional[str],
    category_raw: Optional[str],
    specs: Optional[dict[str, str]],
) -> tuple[Optional[str], list[str]]:
    types = load_product_types().get("product_types", [])
    reasons: list[str] = []
    title_parts = [title or ""]
    if specs:
        title_parts.extend(str(v) for v in specs.values() if v)
    title_blob = " ".join(title_parts)
    matched = _match_type_in_blob(title_blob, types)
    if matched:
        reasons.append(f"title_or_specs_alias:{matched}")
        return matched, reasons

    if category_raw and not _is_discovery_slug(category_raw):
        matched = _match_type_in_blob(category_raw, types)
        if matched:
            reasons.append(f"category_alias:{matched}")
            return matched, reasons
    return None, reasons


def _has_computing_structure(
    *,
    title: Optional[str],
    specs: Optional[dict[str, str]],
) -> bool:
    """CPU/RAM/SSD-like structure supporting a computing product."""
    blob = " ".join(
        [title or ""]
        + ([str(v) for v in (specs or {}).values() if v])
    ).lower()
    cpu = bool(
        re.search(
            r"\b(intel\s+core|amd\s+ryzen|snapdragon|apple\s+m\d|core\s+i[3579]|ryzen\s*\d)\b",
            blob,
            re.I,
        )
    )
    ram = bool(re.search(r"\b\d+\s*gb\s*(ram|mem[oó]ria)?\b", blob, re.I))
    storage = bool(re.search(r"\b(\d+\s*gb\s*ssd|\d+\s*tb\s*ssd|ssd\s*\d+)\b", blob, re.I))
    gpu = bool(re.search(r"\b(rtx|gtx|radeon|geforce|iris\s*xe)\b", blob, re.I))
    return cpu or (ram and storage) or gpu


def classify_mercadolibre_product(
    *,
    title: Optional[str] = None,
    category_raw: Optional[str] = None,
    specs: Optional[dict[str, str]] = None,
    discovery_name: Optional[str] = None,
) -> ClassificationResult:
    """Two-stage classification for Mercado Libre candidates/products."""
    reasons: list[str] = []
    evidence: dict[str, Any] = {
        "title": (title or "")[:240] or None,
        "category_raw": category_raw,
        "discovery_name": discovery_name,
    }

    # Stage 1 — hard negatives always win (even if title contains "notebook"
    # as a false leading word next to TV/power-bank — rare; hard neg phrases
    # are specific). Exception: if title clearly leads with a computing type
    # alias AND hard-neg is a weak secondary token, still prefer hard-neg when
    # the hard-neg phrase is an unambiguous product class (tv, power bank, …).
    neg_hits = _hard_negative_hits(title)
    evidence["hard_negative_hits"] = neg_hits
    if neg_hits:
        reasons.append(f"hard_negative:{','.join(neg_hits)}")
        return ClassificationResult(
            status=EXCLUDED,
            product_type=UNKNOWN,
            confidence=0.95,
            gaming=False,
            hard_negative=True,
            reasons=reasons,
            evidence=evidence,
        )

    # Stage 2 — positive relevance
    ptype, type_reasons = _positive_type(
        title=title, category_raw=category_raw, specs=specs
    )
    reasons.extend(type_reasons)
    structural = _has_computing_structure(title=title, specs=specs)
    evidence["structural_computing"] = structural
    gaming = bool(title and _GAMING_TITLE_RE.search(title))
    if gaming:
        reasons.append("gaming_signal:title")

    # Discovery slug is recorded but never used as type evidence.
    if discovery_name:
        reasons.append("discovery_name_ignored_for_type")

    if ptype and ptype in SUPPORTED_PRODUCT_TYPES:
        conf = 0.9 if structural else 0.75
        if gaming:
            conf = min(0.98, conf + 0.05)
        return ClassificationResult(
            status=VALID,
            product_type=ptype,
            confidence=conf,
            gaming=gaming,
            hard_negative=False,
            reasons=reasons,
            evidence=evidence,
        )

    if structural:
        reasons.append("structural_without_type_alias")
        return ClassificationResult(
            status=UNKNOWN,
            product_type=UNKNOWN,
            confidence=0.4,
            gaming=gaming,
            hard_negative=False,
            reasons=reasons,
            evidence=evidence,
        )

    reasons.append("insufficient_positive_evidence")
    return ClassificationResult(
        status=UNKNOWN,
        product_type=UNKNOWN,
        confidence=0.1,
        gaming=False,
        hard_negative=False,
        reasons=reasons,
        evidence=evidence,
    )


def is_collection_eligible(result: ClassificationResult) -> bool:
    return result.status == VALID and result.product_type in SUPPORTED_PRODUCT_TYPES
