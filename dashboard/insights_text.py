"""Business-facing insight copy from existing analytics objects. No new formulas."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from dashboard.presentation import CHECK_LABELS, TRACKED_PLATFORM_BRANDS

NO_DATA = "No data available for this collection."


def _pct(value: Any, *, digits: int = 1) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def shelf_takeaway(shares: Sequence[Any]) -> tuple[str, str]:
    eligible = [s for s in shares if getattr(s, "share", None) is not None]
    if not eligible:
        return ("No shelf presence data", NO_DATA)
    lead = max(eligible, key=lambda s: float(s.share))
    return (
        f"{lead.value} leads shelf presence",
        f"{_pct(lead.share)} of eligible gaming products",
    )


def shelf_chart_insight(shares: Sequence[Any]) -> str:
    eligible = [s for s in shares if getattr(s, "share", None) is not None]
    if not eligible:
        return NO_DATA
    ranked = sorted(eligible, key=lambda s: float(s.share), reverse=True)
    lead = ranked[0]
    if len(ranked) > 1:
        second = ranked[1]
        return (
            f"{lead.value} leads shelf presence at {_pct(lead.share)}, "
            f"followed by {second.value} at {_pct(second.share)}."
        )
    return f"{lead.value} leads shelf presence at {_pct(lead.share)}."


def search_takeaway(metrics: Sequence[Any]) -> tuple[str, str]:
    tracked = [
        m
        for m in metrics
        if getattr(m, "brand", None) in TRACKED_PLATFORM_BRANDS
        and getattr(m, "share_of_voice", None) is not None
    ]
    if not tracked:
        return ("No search visibility data", NO_DATA)
    lead = max(tracked, key=lambda m: float(m.share_of_voice))
    return (
        f"{lead.brand} leads search visibility",
        f"{_pct(lead.share_of_voice)} of tracked-brand appearances",
    )


def search_chart_insight(metrics: Sequence[Any]) -> str:
    tracked = [
        m
        for m in metrics
        if getattr(m, "brand", None) in TRACKED_PLATFORM_BRANDS
        and getattr(m, "share_of_voice", None) is not None
        and int(getattr(m, "appearances", 0) or 0) > 0
    ]
    if not tracked:
        return NO_DATA
    ranked = sorted(tracked, key=lambda m: float(m.share_of_voice), reverse=True)
    lead = ranked[0]
    if len(ranked) > 1:
        second = ranked[1]
        gap = float(lead.share_of_voice) - float(second.share_of_voice)
        if 0 <= gap <= 0.08:
            return (
                f"{lead.brand} has the highest observed search visibility, "
                f"slightly ahead of {second.brand}."
            )
        return (
            f"{lead.brand} has the highest observed search visibility "
            f"at {_pct(lead.share_of_voice)}."
        )
    return (
        f"{lead.brand} has the highest observed search visibility "
        f"at {_pct(lead.share_of_voice)}."
    )


def compliance_gap_lines(ranked: Sequence[tuple[str, float]]) -> list[str]:
    """Lowest-performing scored checks only. 100% checks are not listed as gaps."""
    if not ranked:
        return ["No scored checks"]
    gaps = [(code, rate) for code, rate in ranked if rate < 1.0][:3]
    if not gaps:
        return ["No significant compliance gaps"]
    lines = []
    for code, rate in gaps:
        label = CHECK_LABELS.get(code, code)
        lines.append(f"{code} — {label}: {rate * 100:.0f}%")
    return lines


def compliance_takeaway(weakest: dict[str, Sequence[tuple[str, float]]]) -> tuple[str, str]:
    candidates: list[tuple[str, str, float]] = []
    for brand in TRACKED_PLATFORM_BRANDS:
        items = list(weakest.get(brand) or [])
        gaps = [(code, rate) for code, rate in items if rate < 1.0]
        if gaps:
            code, rate = gaps[0]
            candidates.append((brand, code, rate))
    if not candidates:
        return ("No significant compliance gaps", "Scored checks are passing, or no scored data is available.")
    brand, code, rate = min(candidates, key=lambda item: item[2])
    label = CHECK_LABELS.get(code, code)
    return (
        f"{brand}'s main compliance gap",
        f"{code} — {label}: {rate * 100:.0f}%",
    )


def attribute_quality_insight(coverage: Sequence[dict[str, Any]]) -> str:
    rows = [
        r
        for r in coverage
        if r.get("coverage_pct") is not None
    ]
    if not rows:
        return NO_DATA
    ranked = sorted(rows, key=lambda r: float(r["coverage_pct"]))
    lowest = ranked[0]
    full = [r for r in rows if float(r["coverage_pct"]) >= 99.95]
    if full and float(lowest["coverage_pct"]) < 99.95:
        names = [str(r["attribute"]).lower() for r in full]
        if len(names) == 1:
            captured = f"{names[0].capitalize()} information is fully captured"
        elif len(names) == 2:
            captured = f"{names[0].capitalize()} and {names[1]} information are fully captured"
        else:
            captured = (
                f"{', '.join(names[:-1])} and {names[-1]} information are fully captured"
            )
        return (
            f"{captured}, while {str(lowest['attribute']).lower()} has the lowest coverage."
        )
    return (
        f"{lowest['attribute']} has the lowest coverage at "
        f"{float(lowest['coverage_pct']):.1f}%."
    )


def no_data_if_empty(text: Optional[str]) -> str:
    return text or NO_DATA
