"""Platform / processor badge detection (DOM/text/alt/title first).

For each product:
1. Determine expected badge families from processor / product attributes.
2. Detect visible badge evidence from page signals.
3. Classify into expected, detected, correct, missing, ambiguous.

OCR is implemented as an optional fallback layer and is disabled by default.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from collector.config_loader import load_badges

logger = logging.getLogger("collector.parsers.badges")

STATUS_EXPECTED = "expected"
STATUS_DETECTED = "detected"
STATUS_CORRECT = "correct"
STATUS_MISSING = "missing"
STATUS_AMBIGUOUS = "ambiguous"

_VALID_STATUSES = frozenset(
    {
        STATUS_EXPECTED,
        STATUS_DETECTED,
        STATUS_CORRECT,
        STATUS_MISSING,
        STATUS_AMBIGUOUS,
    }
)


@dataclass(frozen=True)
class BadgeFamily:
    """Configured platform badge family."""

    code: str
    brand: str
    name: str
    expected_patterns: tuple[str, ...]
    detect_patterns: tuple[str, ...]
    exclude_if_expected: tuple[str, ...] = ()
    detect_requires_context: bool = False
    context_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class BadgeHit:
    """A single family match against evidence."""

    code: str
    brand: str
    name: str
    pattern: str
    evidence: str
    source: str
    ambiguous: bool = False
    reason: Optional[str] = None


@dataclass
class BadgeEvidence:
    """Page / listing evidence used for visible badge detection.

    Prefer structured DOM fields. ``page_text`` is a weaker fallback and may
    produce ambiguous hits for short tokens.
    """

    badge_texts: list[str] = field(default_factory=list)
    img_alts: list[str] = field(default_factory=list)
    img_titles: list[str] = field(default_factory=list)
    element_titles: list[str] = field(default_factory=list)
    element_texts: list[str] = field(default_factory=list)
    page_text: Optional[str] = None
    # Optional OCR layer (not used unless explicitly enabled).
    ocr_texts: list[str] = field(default_factory=list)
    source_url: Optional[str] = None
    screenshot_path: Optional[str] = None


@dataclass
class BadgeEvaluation:
    """Expected vs detected platform-badge evaluation for one product."""

    expected: list[str] = field(default_factory=list)
    detected: list[str] = field(default_factory=list)
    correct: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    hits: list[BadgeHit] = field(default_factory=list)
    expected_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def status_for(self, code: str) -> str:
        if code in self.ambiguous:
            return STATUS_AMBIGUOUS
        if code in self.correct:
            return STATUS_CORRECT
        if code in self.missing:
            return STATUS_MISSING
        if code in self.unexpected or code in self.detected:
            return STATUS_DETECTED
        if code in self.expected:
            return STATUS_EXPECTED
        raise KeyError(f"Badge code not present in evaluation: {code}")


def normalize_space(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def token_boundary_match(alias: str, text: str) -> bool:
    alias = (alias or "").lower().strip()
    text = (text or "").lower()
    if not alias or not text:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None


def _family_table() -> list[BadgeFamily]:
    cfg = load_badges().get("platform_families", []) or []
    families: list[BadgeFamily] = []
    for item in cfg:
        families.append(
            BadgeFamily(
                code=str(item["code"]),
                brand=str(item["brand"]),
                name=str(item["name"]),
                expected_patterns=tuple(
                    str(p).lower() for p in item.get("expected_patterns", []) if p
                ),
                detect_patterns=tuple(
                    str(p).lower() for p in item.get("detect_patterns", []) if p
                ),
                exclude_if_expected=tuple(
                    str(c) for c in item.get("exclude_if_expected", []) if c
                ),
                detect_requires_context=bool(item.get("detect_requires_context", False)),
                context_tokens=tuple(
                    str(t).lower()
                    for t in item.get(
                        "context_tokens",
                        ["badge", "logo", "brand", "emblem", "sticker"],
                    )
                    if t
                ),
            )
        )
    return families


def _product_attribute_blob(
    *,
    processor: Optional[str] = None,
    title: Optional[str] = None,
    specifications: Optional[Any] = None,
    description: Optional[str] = None,
    brand: Optional[str] = None,
) -> str:
    parts: list[str] = []
    for value in (processor, title, description, brand):
        text = normalize_space(value)
        if text:
            parts.append(text)
    if isinstance(specifications, Mapping):
        for key, value in specifications.items():
            if value is None:
                continue
            parts.append(f"{key}: {value}")
    elif specifications is not None:
        parts.append(str(specifications))
    return normalize_space(" ".join(parts))


_APPLE_CONTEXT = re.compile(
    r"\b(?:apple|macbook|imac|mac\s*mini|mac\s*studio|mac\s*pro|apple\s+silicon)\b",
    re.I,
)
_APPLE_M_BARE = re.compile(r"\bm[1-4]\b", re.I)


def _match_patterns(text: str, patterns: Sequence[str]) -> Optional[str]:
    if not text:
        return None
    for pattern in sorted(patterns, key=len, reverse=True):
        if token_boundary_match(pattern, text):
            return pattern
    return None


def _match_expected_for_family(family: BadgeFamily, blob: str) -> Optional[str]:
    """Match expected patterns with Apple bare-M context guard."""
    hit = _match_patterns(blob, family.expected_patterns)
    if not hit:
        return None
    # Bare M1–M4 tokens need Apple/Mac context to expect Apple badge families.
    if family.brand == "Apple" and _APPLE_M_BARE.fullmatch(hit.strip()):
        if not _APPLE_CONTEXT.search(blob):
            return None
    if family.brand == "Apple" and hit.strip() in {"m1", "m2", "m3", "m4"}:
        if not _APPLE_CONTEXT.search(blob):
            return None
    return hit


def _has_context(text: str, pattern: str, tokens: Sequence[str]) -> bool:
    blob = text.lower()
    escaped = re.escape(pattern.lower())
    token_alt = "|".join(re.escape(t) for t in tokens if t)
    if not token_alt:
        return True
    return bool(
        re.search(
            rf"(?:{token_alt}).{{0,48}}{escaped}|{escaped}.{{0,48}}(?:{token_alt})",
            blob,
            re.I,
        )
    )


def expected_badges(
    *,
    processor: Optional[str] = None,
    title: Optional[str] = None,
    specifications: Optional[Any] = None,
    description: Optional[str] = None,
    brand: Optional[str] = None,
) -> list[str]:
    """Return expected platform badge family codes from product attributes."""
    blob = _product_attribute_blob(
        processor=processor,
        title=title,
        specifications=specifications,
        description=description,
        brand=brand,
    )
    if not blob:
        return []

    matched: list[str] = []
    details: dict[str, str] = {}
    families = _family_table()
    for family in families:
        hit = _match_expected_for_family(family, blob)
        if hit:
            matched.append(family.code)
            details[family.code] = hit

    # Exclusion semantics: if family A lists exclude_if_expected: [B] and B is
    # also matched, drop A (A is the less-specific sibling). Example: Core is
    # dropped when Core Ultra matches; Ryzen is dropped when Ryzen AI matches.
    code_to_family = {f.code: f for f in families}
    excluded: set[str] = set()
    for code in matched:
        family = code_to_family[code]
        for sibling in family.exclude_if_expected:
            if sibling in matched:
                excluded.add(code)
                break

    result = [c for c in matched if c not in excluded]
    logger.debug(
        "expected_badges",
        extra={
            "event": "expected_badges",
            "expected": result,
            "matched_patterns": details,
            "excluded": sorted(excluded),
        },
    )
    return result


def _iter_primary_haystacks(evidence: BadgeEvidence) -> list[tuple[str, str]]:
    """Return (source, text) pairs in priority order (no OCR, no page_text)."""
    ordered: list[tuple[str, str]] = []
    mapping = [
        ("badge_text", evidence.badge_texts),
        ("img_alt", evidence.img_alts),
        ("img_title", evidence.img_titles),
        ("element_title", evidence.element_titles),
        ("element_text", evidence.element_texts),
    ]
    for source, values in mapping:
        for value in values:
            text = normalize_space(value)
            if text:
                ordered.append((source, text))
    return ordered


def detect_badges_from_dom(evidence: BadgeEvidence) -> list[BadgeHit]:
    """Detect platform badges from DOM/text/alt/title evidence only."""
    families = _family_table()
    haystacks = _iter_primary_haystacks(evidence)
    hits: list[BadgeHit] = []
    seen_codes: set[str] = set()

    for family in families:
        best: Optional[BadgeHit] = None
        for source, text in haystacks:
            pattern = _match_patterns(text, family.detect_patterns)
            if not pattern:
                continue
            ambiguous = False
            reason = None
            if family.detect_requires_context and not _has_context(
                text, pattern, family.context_tokens
            ):
                # Short / generic tokens without badge context are ambiguous,
                # not confident detections.
                ambiguous = True
                reason = "missing_badge_context"
            candidate = BadgeHit(
                code=family.code,
                brand=family.brand,
                name=family.name,
                pattern=pattern,
                evidence=text[:240],
                source=source,
                ambiguous=ambiguous,
                reason=reason,
            )
            # Prefer non-ambiguous, longer pattern, earlier source.
            if best is None:
                best = candidate
            else:
                if best.ambiguous and not candidate.ambiguous:
                    best = candidate
                elif best.ambiguous == candidate.ambiguous and len(candidate.pattern) > len(
                    best.pattern
                ):
                    best = candidate
            if best and not best.ambiguous:
                break
        if best and best.code not in seen_codes:
            hits.append(best)
            seen_codes.add(best.code)

    # Weaker page_text fallback (may mark short-context hits ambiguous).
    page = normalize_space(evidence.page_text)
    if page:
        for family in families:
            if family.code in seen_codes:
                continue
            pattern = _match_patterns(page, family.detect_patterns)
            if not pattern:
                continue
            needs_context = family.detect_requires_context or len(pattern) <= 5
            ambiguous = needs_context and not _has_context(
                page, pattern, family.context_tokens or ("badge", "logo", "brand")
            )
            hits.append(
                BadgeHit(
                    code=family.code,
                    brand=family.brand,
                    name=family.name,
                    pattern=pattern,
                    evidence=pattern,
                    source="page_text",
                    ambiguous=ambiguous,
                    reason="page_text_weak_match" if ambiguous else None,
                )
            )
            seen_codes.add(family.code)

    return hits


def detect_badges_via_ocr(
    evidence: BadgeEvidence,
    *,
    enabled: Optional[bool] = None,
) -> list[BadgeHit]:
    """Optional OCR fallback layer.

    Disabled by default. When enabled and ``ocr_texts`` are supplied (by an
    upstream OCR step), matches are treated as lower-confidence evidence and
    marked ambiguous unless the pattern is long / specific.
    """
    cfg = load_badges().get("detection", {}) or {}
    if enabled is None:
        enabled = bool(cfg.get("ocr_fallback_enabled", False))
    if not enabled:
        return []
    if not evidence.ocr_texts:
        return []

    families = _family_table()
    hits: list[BadgeHit] = []
    for family in families:
        for text in evidence.ocr_texts:
            blob = normalize_space(text)
            if not blob:
                continue
            pattern = _match_patterns(blob, family.detect_patterns)
            if not pattern:
                continue
            hits.append(
                BadgeHit(
                    code=family.code,
                    brand=family.brand,
                    name=family.name,
                    pattern=pattern,
                    evidence=blob[:240],
                    source="ocr",
                    ambiguous=True,
                    reason="ocr_fallback",
                )
            )
            break
    return hits


def detect_badges(
    evidence: BadgeEvidence,
    *,
    use_ocr_fallback: Optional[bool] = None,
) -> list[BadgeHit]:
    """Detect badges preferring DOM evidence; OCR only as optional fallback."""
    hits = detect_badges_from_dom(evidence)
    present = {h.code for h in hits if not h.ambiguous}
    ocr_hits = detect_badges_via_ocr(evidence, enabled=use_ocr_fallback)
    for hit in ocr_hits:
        if hit.code in present:
            continue
        # Only fill gaps; never override a confident DOM hit.
        if any(h.code == hit.code for h in hits):
            continue
        hits.append(hit)
    return hits


def evaluate_badges(
    *,
    processor: Optional[str] = None,
    title: Optional[str] = None,
    specifications: Optional[Any] = None,
    description: Optional[str] = None,
    brand: Optional[str] = None,
    evidence: Optional[BadgeEvidence] = None,
    use_ocr_fallback: Optional[bool] = None,
) -> BadgeEvaluation:
    """Compute expected / detected / correct / missing / ambiguous sets."""
    expected = expected_badges(
        processor=processor,
        title=title,
        specifications=specifications,
        description=description,
        brand=brand,
    )
    evidence = evidence or BadgeEvidence()
    hits = detect_badges(evidence, use_ocr_fallback=use_ocr_fallback)

    confident = [h for h in hits if not h.ambiguous]
    ambiguous_hits = [h for h in hits if h.ambiguous]

    detected = list(dict.fromkeys(h.code for h in confident))
    ambiguous = list(dict.fromkeys(h.code for h in ambiguous_hits if h.code not in detected))

    expected_set = set(expected)
    detected_set = set(detected)
    correct = [c for c in expected if c in detected_set]
    missing = [c for c in expected if c not in detected_set]
    unexpected = [c for c in detected if c not in expected_set]

    # Ambiguous expected badges that never got a confident hit stay ambiguous
    # (not missing) when the only evidence was weak.
    ambiguous_expected = [c for c in expected if c in ambiguous and c in missing]
    missing = [c for c in missing if c not in ambiguous]
    # Keep ambiguous list stable: include weak detections + ambiguous expected.
    ambiguous = list(dict.fromkeys(ambiguous + ambiguous_expected))

    family_by_code = {f.code: f for f in _family_table()}
    expected_details: dict[str, dict[str, Any]] = {}
    for code in expected:
        fam = family_by_code.get(code)
        expected_details[code] = {
            "brand": fam.brand if fam else None,
            "name": fam.name if fam else None,
            "status_hint": STATUS_EXPECTED,
        }

    notes: list[str] = []
    if not expected and not detected and not ambiguous:
        notes.append("no_platform_badge_signals")
    if unexpected:
        notes.append(f"unexpected_detected:{','.join(unexpected)}")

    return BadgeEvaluation(
        expected=expected,
        detected=detected,
        correct=correct,
        missing=missing,
        ambiguous=ambiguous,
        unexpected=unexpected,
        hits=hits,
        expected_details=expected_details,
        notes=notes,
    )


def evaluation_rows(evaluation: BadgeEvaluation) -> list[dict[str, Any]]:
    """Flatten an evaluation into rows suitable for the ``badges`` table.

    One row per involved family code with ``relevance_notes`` carrying the
    status (correct / missing / detected / ambiguous).
    """
    family_by_code = {f.code: f for f in _family_table()}
    hit_by_code = {h.code: h for h in evaluation.hits}
    codes = list(
        dict.fromkeys(
            evaluation.correct
            + evaluation.missing
            + evaluation.unexpected
            + evaluation.ambiguous
            + evaluation.expected
            + evaluation.detected
        )
    )
    rows: list[dict[str, Any]] = []
    for code in codes:
        fam = family_by_code.get(code)
        try:
            status = evaluation.status_for(code)
        except KeyError:
            continue
        hit = hit_by_code.get(code)
        if status == STATUS_MISSING:
            badge_text = f"missing:{fam.name if fam else code}"
        elif hit:
            badge_text = hit.evidence
        else:
            badge_text = fam.name if fam else code
        notes_parts = [f"status={status}"]
        if fam:
            notes_parts.append(f"brand={fam.brand}")
            notes_parts.append(f"family={fam.name}")
        if hit:
            notes_parts.append(f"source={hit.source}")
            notes_parts.append(f"pattern={hit.pattern}")
            if hit.reason:
                notes_parts.append(f"reason={hit.reason}")
        if code in evaluation.expected:
            notes_parts.append("expected=true")
        if code in evaluation.detected:
            notes_parts.append("detected=true")
        rows.append(
            {
                "badge_code": code,
                "badge_text": badge_text,
                "is_relevant": True,
                "relevance_notes": "; ".join(notes_parts),
                "status": status,
            }
        )
    return rows


def detect_promotional_badges(texts: Iterable[str]) -> list[dict[str, Any]]:
    """Detect promotional badges from free-text snippets (config ``badges``)."""
    cfg = load_badges()
    promo = cfg.get("badges", []) or []
    relevance = cfg.get("relevance_rules", {}) or {}
    relevant = set(relevance.get("relevant_codes", []) or [])
    contextual = set(relevance.get("contextual_codes", []) or [])

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    haystacks = [normalize_space(t) for t in texts if normalize_space(t)]
    for item in promo:
        code = str(item.get("code", ""))
        if not code or code in seen:
            continue
        patterns = [str(p).lower() for p in item.get("patterns", []) if p]
        matched_text: Optional[str] = None
        matched_pattern: Optional[str] = None
        for text in haystacks:
            pattern = _match_patterns(text, patterns)
            if pattern:
                matched_text = text
                matched_pattern = pattern
                break
        if not matched_text:
            continue
        seen.add(code)
        is_relevant = code in relevant
        notes = item.get("relevance") or item.get("name")
        if code in contextual:
            notes = f"contextual; {notes}"
        results.append(
            {
                "badge_code": code,
                "badge_text": matched_text[:240],
                "is_relevant": is_relevant,
                "relevance_notes": (
                    f"status={STATUS_DETECTED}; promotional; "
                    f"pattern={matched_pattern}; {notes}"
                ),
                "status": STATUS_DETECTED,
            }
        )
    return results


__all__ = [
    "STATUS_EXPECTED",
    "STATUS_DETECTED",
    "STATUS_CORRECT",
    "STATUS_MISSING",
    "STATUS_AMBIGUOUS",
    "BadgeFamily",
    "BadgeHit",
    "BadgeEvidence",
    "BadgeEvaluation",
    "expected_badges",
    "detect_badges_from_dom",
    "detect_badges_via_ocr",
    "detect_badges",
    "evaluate_badges",
    "evaluation_rows",
    "detect_promotional_badges",
    "normalize_space",
    "token_boundary_match",
]
