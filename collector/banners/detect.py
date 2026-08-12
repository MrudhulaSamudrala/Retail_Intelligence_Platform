"""Pure homepage banner detection helpers (no Playwright / no network)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

TRACKED_BRANDS = ("Intel", "AMD", "Qualcomm", "Apple")
UNKNOWN = "UNKNOWN"
AMBIGUOUS = "AMBIGUOUS"

# Strong brand token-boundary patterns (prefer specific phrases).
_BRAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Intel", re.compile(r"(?<![a-z0-9])intel(?:\s+core(?:\s+ultra)?)?(?![a-z0-9])", re.I)),
    ("AMD", re.compile(r"(?<![a-z0-9])amd(?:\s+ryzen(?:\s+ai)?)?(?![a-z0-9])", re.I)),
    ("AMD", re.compile(r"(?<![a-z0-9])ryzen(?:\s+ai)?(?![a-z0-9])", re.I)),
    ("Qualcomm", re.compile(r"(?<![a-z0-9])qualcomm(?![a-z0-9])", re.I)),
    ("Qualcomm", re.compile(r"(?<![a-z0-9])snapdragon(?![a-z0-9])", re.I)),
    ("Apple", re.compile(r"(?<![a-z0-9])apple(?:\s+silicon)?(?![a-z0-9])", re.I)),
    ("Apple", re.compile(r"(?<![a-z0-9])macbook(?![a-z0-9])", re.I)),
    ("Apple", re.compile(r"(?<![a-z0-9])m[1-4](?:\s*(?:pro|max|ultra))?(?![a-z0-9])", re.I)),
]


@dataclass
class DetectedBanner:
    """One observed homepage promotional banner with preserved evidence."""

    brand: str
    banner_text: Optional[str] = None
    discount_text: Optional[str] = None
    badge_text: Optional[str] = None
    link_present: bool = False
    link_url: Optional[str] = None
    source_url: Optional[str] = None
    evidence_text: Optional[str] = None
    selector: Optional[str] = None
    detection_method: Optional[str] = None
    screenshot_path: Optional[str] = None
    banner_position: Optional[int] = None
    is_tracked_brand: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def load_banner_config() -> dict[str, Any]:
    path = CONFIG_DIR / "banners.yaml"
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("banners.yaml must be a mapping")
    return data


def _compile_patterns(raw: list[str] | None) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in (raw or [])]


def extract_discount_text(text: Optional[str], *, config: dict[str, Any] | None = None) -> Optional[str]:
    if not text:
        return None
    cfg = config or load_banner_config()
    for pattern in _compile_patterns(cfg.get("discount_patterns")):
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def extract_badge_text(text: Optional[str], *, config: dict[str, Any] | None = None) -> Optional[str]:
    if not text:
        return None
    cfg = config or load_banner_config()
    for pattern in _compile_patterns(cfg.get("badge_patterns")):
        match = pattern.search(text)
        if match:
            return match.group(0).strip()
    return None


def is_excluded_region(
    *,
    tag: Optional[str] = None,
    class_name: Optional[str] = None,
    role: Optional[str] = None,
    selector: Optional[str] = None,
    ancestor_hints: Optional[list[str]] = None,
    config: dict[str, Any] | None = None,
) -> bool:
    """True when the node looks like nav/footer/product-card/search — not a banner."""
    cfg = config or load_banner_config()
    blob_parts = [
        (tag or "").lower(),
        (class_name or "").lower(),
        (role or "").lower(),
        (selector or "").lower(),
        " ".join(ancestor_hints or []).lower(),
    ]
    blob = " ".join(blob_parts)

    # Hard exclusions (product / nav / footer / search)
    hard = [
        "item-cell",
        "product-card",
        "goods-list",
        "item-container",
        "search-result",
        "ui-search-result",
        "nav-item",
        "menu-item",
        "footer",
        "breadcrumb",
    ]
    if any(h in blob for h in hard):
        return True
    if (tag or "").lower() in {"nav", "footer", "table"}:
        return True
    if (role or "").lower() == "navigation":
        return True

    # Config exclude selector tokens
    for exc in cfg.get("exclude_selectors") or []:
        token = re.sub(r"[^\w\-]+", " ", str(exc).lower()).strip()
        for part in token.split():
            if len(part) >= 4 and part in blob:
                # Avoid over-matching generic words like "class"
                if part in {"class", "role", "data", "testid"}:
                    continue
                if part in {"item", "cell", "product", "search", "footer", "nav", "navigation"}:
                    return True
                if "item-cell" in str(exc).lower() and "item-cell" in blob:
                    return True
    return False


def detect_brand_from_evidence(
    *,
    text: Optional[str] = None,
    aria_label: Optional[str] = None,
    alt: Optional[str] = None,
    title: Optional[str] = None,
    evidence_priority: Optional[list[str]] = None,
) -> tuple[str, str, Optional[str]]:
    """Return (brand, detection_method, matched_evidence).

    Uses token-boundary patterns. Conflicting brands → AMBIGUOUS.
    No confident match → UNKNOWN. Never guesses.
    """
    cfg = load_banner_config()
    priority = evidence_priority or list(
        (cfg.get("detection") or {}).get("evidence_priority") or ["text", "aria_label", "alt", "title"]
    )
    # DOM structure is handled by candidate selection; brand comes from text layers.
    layers: list[tuple[str, Optional[str]]] = [
        ("text", text),
        ("aria_label", aria_label),
        ("alt", alt),
        ("title", title),
    ]
    ordered = []
    for name in priority:
        if name == "dom":
            continue
        for layer_name, value in layers:
            if layer_name == name:
                ordered.append((layer_name, value))
                break
    for layer_name, value in layers:
        if (layer_name, value) not in ordered:
            ordered.append((layer_name, value))

    for method, value in ordered:
        if not value or not str(value).strip():
            continue
        hits: list[str] = []
        for brand, pattern in _BRAND_PATTERNS:
            if pattern.search(value) and brand not in hits:
                hits.append(brand)
        if len(hits) > 1:
            return AMBIGUOUS, method, value.strip()[:500]
        if len(hits) == 1:
            return hits[0], method, value.strip()[:500]
    return UNKNOWN, "text", (text or aria_label or alt or title or "")[:500] or None


def process_banner_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_url: Optional[str] = None,
    config: dict[str, Any] | None = None,
) -> list[DetectedBanner]:
    """Convert raw DOM candidates into DetectedBanner rows (no invention)."""
    cfg = config or load_banner_config()
    tracked = set(cfg.get("tracked_brands") or TRACKED_BRANDS)
    out: list[DetectedBanner] = []
    seen_fingerprints: set[str] = set()

    for idx, raw in enumerate(candidates, start=1):
        if raw.get("excluded") or is_excluded_region(
            tag=raw.get("tag"),
            class_name=raw.get("class_name"),
            role=raw.get("role"),
            selector=raw.get("selector"),
            ancestor_hints=raw.get("ancestor_hints"),
            config=cfg,
        ):
            continue

        text = (raw.get("text") or "").strip() or None
        aria = (raw.get("aria_label") or "").strip() or None
        alt = (raw.get("alt") or "").strip() or None
        title = (raw.get("title") or "").strip() or None
        href = (raw.get("href") or "").strip() or None

        # Require some promotional surface signal or brand evidence in a banner container
        evidence_blob = " ".join(x for x in [text, aria, alt, title] if x)
        if not evidence_blob.strip():
            continue

        brand, method, matched = detect_brand_from_evidence(
            text=text, aria_label=aria, alt=alt, title=title
        )
        discount = extract_discount_text(evidence_blob, config=cfg)
        badge = extract_badge_text(evidence_blob, config=cfg)
        banner_text = text or aria or alt or title
        if banner_text:
            banner_text = re.sub(r"\s+", " ", banner_text).strip()[:1000]

        fingerprint = "|".join(
            [
                brand,
                (banner_text or "")[:120],
                href or "",
                (raw.get("selector") or "")[:120],
            ]
        )
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)

        out.append(
            DetectedBanner(
                brand=brand,
                banner_text=banner_text,
                discount_text=discount,
                badge_text=badge,
                link_present=bool(href),
                link_url=href,
                source_url=source_url or raw.get("source_url"),
                evidence_text=matched or evidence_blob[:1000],
                selector=raw.get("selector"),
                detection_method=method,
                screenshot_path=raw.get("screenshot_path"),
                banner_position=raw.get("position") or idx,
                is_tracked_brand=brand in tracked,
                details={
                    "tag": raw.get("tag"),
                    "class_name": raw.get("class_name"),
                    "role": raw.get("role"),
                    "aria_label": aria,
                    "alt": alt,
                    "title_attr": title,
                    "ocr_used": False,
                },
            )
        )
    return out
