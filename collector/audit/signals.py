"""Deterministic signal helpers for retailer audit checks."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

from collector.config_loader import load_brands, load_oems

UNKNOWN = "UNKNOWN"

# Brand-specific processor / generation lines (brief: S1/P1/P3).
_PROCESSOR_LINE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Intel": [
        re.compile(r"\bintel\s+core\s+ultra\d*\b", re.I),
        re.compile(r"\bintel\s+core\b", re.I),
        re.compile(r"\bcore\s+ultra\d*\b", re.I),
        re.compile(r"\bcore\s+i[3579](?:-\d+\w*)?\b", re.I),
        re.compile(r"\bintel\s+(?:pentium|celeron|xeon)\b", re.I),
    ],
    "AMD": [
        re.compile(r"\bamd\s+ryzen\s+ai\b", re.I),
        re.compile(r"\bamd\s+ryzen\b", re.I),
        re.compile(r"\bryzen\s+ai\b", re.I),
        re.compile(r"\bryzen\s+\d\b", re.I),
        re.compile(r"\bryzen\b", re.I),
        re.compile(r"\bthreadripper\b", re.I),
        re.compile(r"\bathlon\b", re.I),
    ],
    "Qualcomm": [
        re.compile(r"\bqualcomm\s+snapdragon\b", re.I),
        re.compile(r"\bsnapdragon\s+x\s+(?:elite|plus)\b", re.I),
        re.compile(r"\bsnapdragon\b", re.I),
    ],
    "Apple": [
        re.compile(r"\bapple\s+silicon\b", re.I),
        re.compile(r"\bapple\s+m[1-4](?:\s*(?:pro|max|ultra))?\b", re.I),
        re.compile(r"\bm[1-4]\s*(?:pro|max|ultra)\b", re.I),
        re.compile(r"\bm[1-4]\b", re.I),
    ],
}


@lru_cache(maxsize=1)
def load_compliance() -> dict[str, Any]:
    from collector.config_loader import CONFIG_DIR
    import yaml

    path = CONFIG_DIR / "compliance.yaml"
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("compliance.yaml must be a mapping")
    return data


def token_boundary_match(alias: str, text: str) -> bool:
    alias = (alias or "").lower().strip()
    text = (text or "").lower()
    if not alias or not text:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None


def normalize_space(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def is_auditable_brand(brand: Optional[str]) -> bool:
    return bool(brand) and brand not in {UNKNOWN, "unknown", "Unknown"}


def is_auditable_oem(oem: Optional[str]) -> bool:
    return bool(oem) and oem not in {UNKNOWN, "unknown", "Unknown", "None"}


def brand_name_aliases(brand: str) -> list[str]:
    """Return brand display name + configured aliases (longest first)."""
    aliases = {brand.lower()}
    for item in load_brands().get("brands", []):
        if item.get("name") == brand:
            for a in item.get("aliases", []):
                aliases.add(str(a).lower())
            break
    # Prefer longer aliases first for evidence snippets.
    return sorted(aliases, key=len, reverse=True)


def brand_name_in_text(brand: str, text: Optional[str]) -> Optional[str]:
    blob = normalize_space(text)
    if not blob:
        return None
    for alias in brand_name_aliases(brand):
        if token_boundary_match(alias, blob):
            return alias
    return None


def processor_line_in_text(brand: str, text: Optional[str]) -> Optional[str]:
    blob = normalize_space(text)
    if not blob:
        return None
    patterns = _PROCESSOR_LINE_PATTERNS.get(brand, [])
    for pattern in patterns:
        match = pattern.search(blob)
        if match:
            return match.group(0)
    # Also accept configured processor families from brands.yaml
    for item in load_brands().get("brands", []):
        if item.get("name") != brand:
            continue
        for family in sorted(item.get("processor_families", []), key=len, reverse=True):
            if token_boundary_match(str(family), blob):
                return str(family)
    return None


def brand_or_processor_in_text(brand: str, text: Optional[str]) -> dict[str, Any]:
    name_hit = brand_name_in_text(brand, text)
    line_hit = processor_line_in_text(brand, text)
    return {
        "brand_name_match": name_hit,
        "processor_line_match": line_hit,
        "matched": bool(name_hit or line_hit),
    }


def _pattern_list_for_brand(section: str, brand: str) -> list[str]:
    cfg = load_compliance().get(section, {}) or {}
    patterns = cfg.get(brand) or cfg.get(brand.upper()) or []
    return [str(p).lower() for p in patterns]


def brand_badge_match(
    brand: str,
    *,
    badge_texts: Optional[list[str]] = None,
    page_text: Optional[str] = None,
) -> dict[str, Any]:
    """Detect brand badge signals from explicit badge strings and/or page text."""
    patterns = _pattern_list_for_brand("brand_badge_patterns", brand)
    haystacks = [normalize_space(t) for t in (badge_texts or []) if normalize_space(t)]
    page = normalize_space(page_text)
    matched_pattern: Optional[str] = None
    matched_from: Optional[str] = None

    for pattern in sorted(patterns, key=len, reverse=True):
        for badge in haystacks:
            if token_boundary_match(pattern, badge):
                return {
                    "matched": True,
                    "pattern": pattern,
                    "source": "badge_text",
                    "evidence": badge[:240],
                }
        if page and token_boundary_match(pattern, page):
            # Page-wide weak hits alone are not enough for badges unless pattern
            # is longer than a bare vendor token OR appears near badge-like markers.
            if len(pattern) <= 3 and not haystacks:
                continue
            matched_pattern = pattern
            matched_from = "page_text"
            break

    if matched_pattern and matched_from == "page_text":
        # Require badge-ish context for page_text-only matches of short tokens.
        if len(matched_pattern) <= 4:
            context_ok = bool(
                re.search(
                    rf"(?:badge|logo|brand|emblem|sticker).{{0,40}}{re.escape(matched_pattern)}"
                    rf"|{re.escape(matched_pattern)}.{{0,40}}(?:badge|logo|brand|emblem|sticker)",
                    page,
                    re.I,
                )
            )
            if not context_ok:
                return {"matched": False, "pattern": None, "source": None, "evidence": None}
        return {
            "matched": True,
            "pattern": matched_pattern,
            "source": "page_text",
            "evidence": matched_pattern,
        }

    return {"matched": False, "pattern": None, "source": None, "evidence": None}


def media_signal_match(
    *,
    patterns: list[str],
    signals: list[str],
) -> dict[str, Any]:
    for signal in signals:
        text = normalize_space(signal)
        if not text:
            continue
        for pattern in sorted(patterns, key=len, reverse=True):
            if token_boundary_match(pattern, text):
                return {
                    "matched": True,
                    "pattern": pattern,
                    "evidence": text[:240],
                }
    return {"matched": False, "pattern": None, "evidence": None}


def brand_rich_media_match(brand: str, signals: list[str]) -> dict[str, Any]:
    patterns = _pattern_list_for_brand("brand_badge_patterns", brand)
    # Prefer richer media cues; still allow brand patterns against alt/src/labels.
    return media_signal_match(patterns=patterns, signals=signals)


def oem_rich_media_match(oem: str, signals: list[str]) -> dict[str, Any]:
    cfg = load_compliance().get("oem_rich_media_patterns", {}) or {}
    patterns = [str(p).lower() for p in (cfg.get(oem) or [])]
    if not patterns:
        # Fall back to OEM aliases from oems.yaml
        for item in load_oems().get("oems", []):
            if item.get("name") == oem:
                patterns = [str(a).lower() for a in item.get("aliases", [])]
                patterns.append(oem.lower())
                break
    return media_signal_match(patterns=patterns, signals=signals)


def specs_to_text(specs: Optional[dict[str, str]]) -> str:
    if not specs:
        return ""
    return " ".join(f"{k}: {v}" for k, v in specs.items() if v)
