"""Deterministic Brand and OEM classification (no LLM).

Brand and OEM are classified independently from ordered text evidence.
Ambiguous or conflicting evidence yields UNKNOWN with an explicit reason.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from collector.config_loader import load_oems

UNKNOWN = "UNKNOWN"
OTHER = "OTHER"
TRACKED_BRANDS = frozenset({"Intel", "AMD", "Qualcomm", "Apple"})

logger = logging.getLogger("collector.classification")

# Strong processor / SoC patterns (preferred). Longer patterns are listed first.
_BRAND_STRONG: list[tuple[str, re.Pattern[str]]] = [
    # Intel
    ("Intel", re.compile(r"\bintel\s+core\s+ultra\d*\b", re.I)),
    ("Intel", re.compile(r"\bintel\s+core\b", re.I)),
    ("Intel", re.compile(r"\bcore\s+ultra\d*\b", re.I)),
    ("Intel", re.compile(r"\bcore\s+i[3579]\b", re.I)),
    ("Intel", re.compile(r"\bintel\s+(?:pentium|celeron|xeon)\b", re.I)),
    # AMD
    ("AMD", re.compile(r"\bamd\s+ryzen\s+ai\b", re.I)),
    ("AMD", re.compile(r"\bamd\s+ryzen\b", re.I)),
    ("AMD", re.compile(r"\bryzen\s+ai\b", re.I)),
    ("AMD", re.compile(r"\bryzen\b", re.I)),
    ("AMD", re.compile(r"\bthreadripper\b", re.I)),
    ("AMD", re.compile(r"\bathlon\b", re.I)),
    # Qualcomm
    ("Qualcomm", re.compile(r"\bqualcomm\s+snapdragon\b", re.I)),
    ("Qualcomm", re.compile(r"\bsnapdragon\s+x2\b", re.I)),
    ("Qualcomm", re.compile(r"\bsnapdragon\s+x\s*2\b", re.I)),
    ("Qualcomm", re.compile(r"\bsnapdragon\s+x\s+(?:elite|plus)\b", re.I)),
    ("Qualcomm", re.compile(r"\bsnapdragon\b", re.I)),
    # Apple Silicon / M-series (M1–M5). Bare "Apple iPad" is not platform evidence.
    ("Apple", re.compile(r"\bapple\s+silicon\b", re.I)),
    ("Apple", re.compile(r"\bapple\s+m[1-5](?:\s*(?:pro|max|ultra))?\b", re.I)),
    ("Apple", re.compile(r"\bm[1-5]\s*(?:pro|max|ultra)\b", re.I)),
]

# Weaker vendor tokens — only trusted in processor / manufacturer fields.
_BRAND_WEAK_VENDOR: list[tuple[str, re.Pattern[str]]] = [
    ("Intel", re.compile(r"\bintel\b", re.I)),
    ("AMD", re.compile(r"\bamd\b", re.I)),
    ("Qualcomm", re.compile(r"\bqualcomm\b", re.I)),
    ("Apple", re.compile(r"\bapple\b", re.I)),
]

_APPLE_CONTEXT = re.compile(
    r"\b(?:apple|macbook|imac|mac\s*mini|mac\s*studio|mac\s*pro|apple\s+silicon)\b",
    re.I,
)
_APPLE_M_BARE = re.compile(r"\bm[1-5]\b", re.I)

# Identified chip/SoC vendors that are not tracked analytical brands → OTHER.
_BRAND_OTHER_SOC: list[re.Pattern[str]] = [
    re.compile(r"\bmediatek\b", re.I),
    re.compile(r"\bkompanio\b", re.I),
    re.compile(r"\bdimensity\b", re.I),
    re.compile(r"\bhelio\s*[gp]?\d", re.I),
    re.compile(r"\bexynos\b", re.I),
    re.compile(r"\bunisoc\b", re.I),
    re.compile(r"\bspreadtrum\b", re.I),
    re.compile(r"\bkirin\b", re.I),
    re.compile(r"\bhisilicon\b", re.I),
    re.compile(r"\bgoogle\s+tensor\b", re.I),
    re.compile(r"\brockchip\b", re.I),
]
_GPU_OTHER_VENDOR = re.compile(r"\b(nvidia|geforce)\b", re.I)
# Software / accessory phrases must not count as Apple OEM evidence.
_OEM_APPLE_NOISE = re.compile(
    r"\b("
    r"office\s*365|microsoft\s+office|apple\s*care|applecare|"
    r"apple\s+music|apple\s+tv\+|itunes"
    r")\b",
    re.I,
)
_AMD_GPU_EVIDENCE = re.compile(
    r"\b(amd\s+radeon|radeon\s+rx|radeon\b|gpu\s+series:\s*amd|"
    r"chipset\s+manufacturer:\s*amd)\b",
    re.I,
)
_SYSTEM_COMPUTER_RE = re.compile(
    r"\b(laptops?|notebooks?|chromebooks?|macbooks?|ultrabooks?|"
    r"desktop\s+pcs?|desktop\s+computers?|gaming\s+desktops?|"
    r"pre-?built|all[- ]in[- ]ones?|mini\s*pcs?|\bimacs?\b|mac\s*minis?)\b",
    re.I,
)
_DISCRETE_GPU_CARD_RE = re.compile(
    r"\b(graphics\s+cards?|video\s+cards?|gpu\s+boards?|"
    r"geforce\s+(?:rtx|gtx)|nvidia\s+(?:geforce|rtx|gtx)|"
    r"radeon\s+rx|radeon\s+\d{2,4}|rx\s+\d{3,4}|"
    r"rtx\s+\d{3,4}|gtx\s+\d{3,4})\b",
    re.I,
)


def is_system_computer_title(title: Optional[str]) -> bool:
    """True when the listing is a complete computer, not a CPU/GPU component."""
    return bool(title and _SYSTEM_COMPUTER_RE.search(title))


def is_discrete_gpu_product(
    *,
    title: Optional[str] = None,
    product_type: Optional[str] = None,
    gpu: Optional[str] = None,
) -> bool:
    """True for a graphics-card listing (not a notebook/desktop that merely has a GPU)."""
    if (product_type or "").lower() == "gpu":
        return True
    if is_system_computer_title(title):
        return False
    blob = f"{title or ''} {gpu or ''}"
    return bool(_DISCRETE_GPU_CARD_RE.search(blob))


@dataclass(frozen=True)
class ClassificationResult:
    """Independent Brand and OEM classification outcome."""

    brand: str
    oem: str
    brand_reason: str
    oem_reason: str


def _specs_to_text(specifications: Optional[Any]) -> str:
    if specifications is None:
        return ""
    if isinstance(specifications, Mapping):
        parts: list[str] = []
        for key, value in specifications.items():
            if value is None:
                continue
            parts.append(f"{key}: {value}")
        return " ".join(parts)
    return str(specifications)


def _normalize_space(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _token_boundary_match(alias: str, text: str) -> bool:
    """Match alias with alphanumeric boundaries (prevents hp∈hdmi)."""
    alias = alias.lower().strip()
    text = text.lower()
    if not alias or not text:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _find_brands_in_text(
    text: str,
    *,
    patterns: Sequence[tuple[str, re.Pattern[str]]],
    allow_bare_apple_m: bool = False,
) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for name, pattern in patterns:
        if pattern.search(text) and name not in found:
            found.append(name)
    if allow_bare_apple_m and "Apple" not in found:
        if _APPLE_CONTEXT.search(text) and _APPLE_M_BARE.search(text):
            found.append("Apple")
    return found


def _resolve_unique(
    candidates: list[str],
    *,
    field_label: str,
) -> tuple[Optional[str], Optional[str]]:
    unique = list(dict.fromkeys(candidates))
    if not unique:
        return None, None
    if len(unique) > 1:
        return None, f"conflicting_signals_in_{field_label}:{','.join(unique)}"
    return unique[0], f"matched_in_{field_label}"


def _other_soc_in_text(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in _BRAND_OTHER_SOC)


def classify_brand(
    *,
    title: Optional[str] = None,
    processor: Optional[str] = None,
    manufacturer: Optional[str] = None,
    specifications: Optional[Any] = None,
    description: Optional[str] = None,
    product_type: Optional[str] = None,
    gpu: Optional[str] = None,
) -> tuple[str, str]:
    """Classify Brand independently. Returns ``(brand, reason)``.

    Tracked values: Intel, AMD, Qualcomm, Apple.
    OTHER: a non-tracked chip/SoC was reliably identified.
    UNKNOWN: insufficient or conflicting evidence (never invent a tracked brand).
    """
    # Ordered evidence. Processor is highest trust; description is strong-patterns only.
    stages: list[tuple[str, str, Sequence[tuple[str, re.Pattern[str]]], bool]] = [
        (
            "processor",
            _normalize_space(processor),
            _BRAND_STRONG + _BRAND_WEAK_VENDOR,
            True,
        ),
        ("title", _normalize_space(title), _BRAND_STRONG, True),
        (
            "manufacturer",
            _normalize_space(manufacturer),
            _BRAND_STRONG + _BRAND_WEAK_VENDOR,
            True,
        ),
        (
            "specifications",
            _normalize_space(_specs_to_text(specifications)),
            _BRAND_STRONG,
            True,
        ),
        ("description", _normalize_space(description), _BRAND_STRONG, False),
    ]

    for label, text, patterns, allow_bare_m in stages:
        if not text:
            continue
        hits = _find_brands_in_text(
            text, patterns=patterns, allow_bare_apple_m=allow_bare_m
        )
        value, reason = _resolve_unique(hits, field_label=label)
        if reason and reason.startswith("conflicting_"):
            logger.info(
                "brand_unknown",
                extra={"event": "brand_unknown", "reason": reason},
            )
            return UNKNOWN, reason
        if value and reason:
            if value == "Qualcomm" and label == "title":
                return value, "snapdragon_title"
            if value == "Apple" and label == "processor":
                return value, "apple_silicon_processor"
            return value, reason

    other_stages: list[tuple[str, str]] = [
        ("processor", _normalize_space(processor)),
        ("title", _normalize_space(title)),
        ("manufacturer", _normalize_space(manufacturer)),
        ("specifications", _normalize_space(_specs_to_text(specifications))),
    ]
    for label, text in other_stages:
        if not text:
            continue
        if _other_soc_in_text(text):
            return OTHER, f"matched_other_soc_in_{label}"
        if (product_type or "").lower() == "gpu" and _GPU_OTHER_VENDOR.search(text):
            return OTHER, f"matched_other_gpu_vendor_in_{label}"

    # GPU platform brand is independent of CPU/SoC fields. Do not run this
    # on notebooks/desktops that merely mention a discrete GPU.
    if is_discrete_gpu_product(title=title, product_type=product_type, gpu=gpu):
        gpu_stages: list[tuple[str, str]] = [
            ("gpu", _normalize_space(gpu)),
            ("title", _normalize_space(title)),
            ("specifications", _normalize_space(_specs_to_text(specifications))),
        ]
        for label, text in gpu_stages:
            if not text:
                continue
            if _AMD_GPU_EVIDENCE.search(text) or (
                label == "gpu" and re.search(r"\bamd\b", text, re.I)
            ):
                if re.search(r"chipset\s+manufacturer:\s*amd", text, re.I) or (
                    label == "gpu" and re.search(r"\bamd\b", text, re.I)
                ):
                    return "AMD", "gpu_chipset_manufacturer"
                return "AMD", "radeon_gpu_series"
            if _GPU_OTHER_VENDOR.search(text):
                return OTHER, f"matched_other_gpu_vendor_in_{label}"

    reason = "insufficient_brand_evidence"
    logger.info("brand_unknown", extra={"event": "brand_unknown", "reason": reason})
    return UNKNOWN, reason


def _oem_alias_table() -> list[tuple[str, list[str]]]:
    configured = load_oems().get("oems", [])
    table: list[tuple[str, list[str]]] = []
    for item in configured:
        name = item["name"]
        aliases = {str(a).lower() for a in item.get("aliases", []) if a}
        aliases.add(name.lower())
        code = str(item.get("code", "")).lower()
        if code:
            aliases.add(code)
        # ASUS uppercase form is covered by case-insensitive boundary match on "asus".
        table.append((name, sorted(aliases, key=len, reverse=True)))
    return table


def _find_oems_in_text(text: str) -> list[str]:
    if not text:
        return []
    cleaned = _OEM_APPLE_NOISE.sub(" ", text)
    found: list[str] = []
    for name, aliases in _oem_alias_table():
        for alias in aliases:
            if _token_boundary_match(alias, cleaned):
                if name not in found:
                    found.append(name)
                break
    return found


def classify_oem(
    *,
    title: Optional[str] = None,
    processor: Optional[str] = None,
    manufacturer: Optional[str] = None,
    specifications: Optional[Any] = None,
    description: Optional[str] = None,
    product_type: Optional[str] = None,
) -> tuple[str, str]:
    """Classify OEM independently. Returns ``(oem, reason)``.

    ``processor`` is accepted for API symmetry but is never used as OEM evidence.
    """
    _ = processor

    if product_type in {"cpu", "gpu"}:
        reason = "component_product_type_no_system_oem"
        logger.info("oem_unknown", extra={"event": "oem_unknown", "reason": reason})
        return UNKNOWN, reason

    stages: list[tuple[str, str]] = [
        ("manufacturer", _normalize_space(manufacturer)),
        ("title", _normalize_space(title)),
        ("specifications", _normalize_space(_specs_to_text(specifications))),
        ("description", _normalize_space(description)),
    ]

    for label, text in stages:
        if not text:
            continue
        hits = _find_oems_in_text(text)
        value, reason = _resolve_unique(hits, field_label=label)
        if reason and reason.startswith("conflicting_"):
            logger.info("oem_unknown", extra={"event": "oem_unknown", "reason": reason})
            return UNKNOWN, reason
        if value and reason:
            return value, reason

    reason = "insufficient_oem_evidence"
    logger.info("oem_unknown", extra={"event": "oem_unknown", "reason": reason})
    return UNKNOWN, reason


def classify_product(
    *,
    title: Optional[str] = None,
    processor: Optional[str] = None,
    manufacturer: Optional[str] = None,
    specifications: Optional[Any] = None,
    description: Optional[str] = None,
    product_type: Optional[str] = None,
    gpu: Optional[str] = None,
) -> ClassificationResult:
    """Classify Brand and OEM independently from ordered evidence fields."""
    brand, brand_reason = classify_brand(
        title=title,
        processor=processor,
        manufacturer=manufacturer,
        specifications=specifications,
        description=description,
        product_type=product_type,
        gpu=gpu,
    )
    oem, oem_reason = classify_oem(
        title=title,
        processor=processor,
        manufacturer=manufacturer,
        specifications=specifications,
        description=description,
        product_type=product_type,
    )
    return ClassificationResult(
        brand=brand,
        oem=oem,
        brand_reason=brand_reason,
        oem_reason=oem_reason,
    )


def detect_brand(*texts: Optional[str]) -> str:
    """Legacy helper used by older call sites/tests."""
    non_empty = [t for t in texts if t]
    if not non_empty:
        brand, _ = classify_brand()
        return brand
    if len(non_empty) == 1:
        brand, _ = classify_brand(title=non_empty[0])
        return brand
    # Common call shape: title, processor, gpu, category, specs...
    title = non_empty[0]
    processor = non_empty[1] if len(non_empty) > 1 else None
    specs_blob = " ".join(non_empty[2:]) if len(non_empty) > 2 else None
    brand, _ = classify_brand(
        title=title,
        processor=processor,
        specifications=specs_blob,
    )
    return brand


def detect_oem(*texts: Optional[str], product_type: Optional[str] = None) -> str:
    """Legacy helper returning OEM or UNKNOWN (never None)."""
    non_empty = [t for t in texts if t]
    if not non_empty:
        oem, _ = classify_oem(product_type=product_type)
        return oem
    title = non_empty[0]
    rest = " ".join(non_empty[1:]) if len(non_empty) > 1 else None
    oem, _ = classify_oem(
        title=title,
        specifications=rest,
        product_type=product_type,
    )
    return oem
