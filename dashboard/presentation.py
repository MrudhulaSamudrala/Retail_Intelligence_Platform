"""Pure presentation helpers for the dashboard. No analytics formula changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

RETAILER_LABELS = {
    "newegg": "Newegg",
    "mercadolibre": "Mercado Libre",
}

STRATA = (
    "notebook",
    "desktop",
    "workstation",
    "tablet",
    "gpu",
    "cpu",
)

STRATUM_LABELS = {
    "notebook": "Notebook",
    "desktop": "Desktop",
    "workstation": "Workstation",
    "tablet": "Tablet",
    "gpu": "GPU",
    "cpu": "CPU",
}

BRAND_ORDER = ("Intel", "AMD", "Qualcomm", "Apple", "OTHER", "UNKNOWN")
TRACKED_PLATFORM_BRANDS = ("Intel", "AMD", "Qualcomm", "Apple")
CHECK_CODES = ("S1", "S2", "P1", "P2", "P3", "P4", "P5")
CHECK_LABELS = {
    "S1": "Listing title",
    "S2": "Listing badge",
    "P1": "Product title",
    "P2": "Product badge",
    "P3": "Spec table",
    "P4": "Brand media",
    "P5": "OEM media",
}
PASS_COLOR = "#22c55e"
FAIL_COLOR = "#ef4444"
UNKNOWN_COLOR = "#d4d4d4"

RANKED_PARTIAL_BASES = frozenset({"observed_partial", "mixed"})


def retailer_label(code: Optional[str]) -> str:
    if not code:
        return "All retailers"
    return RETAILER_LABELS.get(code, code)


def stratum_label(code: Optional[str]) -> str:
    if not code:
        return "All"
    return STRATUM_LABELS.get(code, code.replace("_", " ").title())


def brand_sort_key(name: str) -> tuple[int, str]:
    try:
        return (BRAND_ORDER.index(name), name)
    except ValueError:
        return (len(BRAND_ORDER), name)


@dataclass(frozen=True)
class CoverageDisplay:
    """Search-collection coverage for one retailer, for compact UI only."""

    retailer_code: str
    observed: Optional[int]
    budget: Optional[int]
    basis: str
    status: str
    headline: str
    detail: str


def format_search_coverage(
    *,
    observed: Optional[int],
    budget: Optional[int],
    basis: str,
) -> tuple[str, str, str]:
    """Return (headline, status, detail).

    COMPLETE may show observed/budget. PARTIAL shows observed count only.
    Empty collections are unavailable — not a fabricated zero.
    """
    basis_n = (basis or "").strip() or "empty"
    if observed is None or basis_n in {"empty", "NO_DATA"}:
        return (
            "Not available for this collection",
            "UNAVAILABLE",
            "This retailer has no current ranked search collection to report.",
        )
    if basis_n == "exact":
        if budget:
            headline = f"{observed} / {budget} positions"
        else:
            headline = f"{observed} positions"
        return (headline, "COMPLETE", "Ranked search coverage is complete for this retailer.")
    if observed == 0 and basis_n in RANKED_PARTIAL_BASES:
        return (
            "Not available for this collection",
            "UNAVAILABLE",
            "This retailer's current collection does not provide ranked search coverage.",
        )
    return (
        f"{observed} observed",
        "PARTIAL",
        "Ranked search data is partial for this retailer.",
    )


def check_visual_status(pass_count: int, fail_count: int, unknown_count: int) -> str:
    """Segment color status. Any FAIL is FAIL; UNKNOWN is never treated as PASS/FAIL."""
    if pass_count + fail_count + unknown_count <= 0:
        return "UNKNOWN"
    if fail_count > 0:
        return "FAIL"
    if pass_count > 0:
        return "PASS"
    return "UNKNOWN"


def format_check_cell(pass_count: int, fail_count: int, unknown_count: int) -> str:
    scored = pass_count + fail_count
    total = scored + unknown_count
    if scored <= 0:
        return "—"
    rate = 100.0 * pass_count / scored
    return f"{rate:.0f}% ({scored}/{total})"


def format_coverage_cell(pass_count: int, fail_count: int, unknown_count: int) -> str:
    scored = pass_count + fail_count
    total = scored + unknown_count
    if total <= 0:
        return "—"
    return f"{scored}/{total}"


def format_check_status_cell(pass_count: int, fail_count: int, unknown_count: int) -> tuple[str, str]:
    """Visible check cell: existing pass rate plus PASS/FAIL/NO DATA marker.

    N/A when there are no scored (PASS/FAIL) observations — never rendered as 0%.
    """
    status = check_visual_status(pass_count, fail_count, unknown_count)
    scored = pass_count + fail_count
    if scored <= 0:
        return "N/A —", "UNKNOWN"
    rate = 100.0 * pass_count / scored
    if status == "PASS":
        return f"{rate:.0f}% ✓", "PASS"
    if status == "FAIL":
        return f"{rate:.0f}% ✕", "FAIL"
    return "N/A —", "UNKNOWN"


def lowest_scored_checks(
    ranked: Sequence[tuple[str, float]],
    *,
    limit: int = 3,
) -> list[tuple[str, float]]:
    """First *limit* checks from an already lowest-to-highest scored list."""
    return list(ranked)[:limit]


def format_center_percent(score: Optional[float]) -> str:
    """Ring/KPI percent text. Missing scores are N/A, never 0%."""
    if score is None:
        return "N/A"
    return f"{float(score) * 100:.0f}%"


def status_color(status: str) -> str:
    if status == "PASS":
        return PASS_COLOR
    if status == "FAIL":
        return FAIL_COLOR
    return UNKNOWN_COLOR


def badge_coverage_state(rate: Optional[float]) -> str:
    """Map observed badge coverage to a compact semantic state. None → N/A."""
    if rate is None:
        return "N/A"
    pct = rate * 100.0 if rate <= 1 else rate
    if pct >= 80:
        return "GOOD"
    if pct >= 50:
        return "PARTIAL"
    return "LOW"


def displayed_compliance_center(score) -> tuple[Optional[float], str]:
    """Ring-center value from existing analytics fields only.

    ``overall_score`` is notebook×0.85 + desktop×0.15 and is None when either
    weighted segment is missing. That is not “no audit data”: notebook/desktop
    segment scores are already computed. Prefer overall, else the available
    segment score. Never invent a new formula or treat UNKNOWN as scored.
    """
    if score is None:
        return None, "No Data"
    overall = getattr(score, "overall_score", None)
    if overall is not None:
        return float(overall), "Pass Rate"
    notebook = getattr(score, "notebook", None)
    if notebook is not None and getattr(notebook, "score", None) is not None:
        return float(notebook.score), "Pass Rate"
    desktop = getattr(score, "desktop", None)
    if desktop is not None and getattr(desktop, "score", None) is not None:
        return float(desktop.score), "Pass Rate"
    others = getattr(score, "other_segments", None) or {}
    for seg in others.values():
        if getattr(seg, "score", None) is not None:
            return float(seg.score), "Pass Rate"
    return None, "No Data"


def brand_score_lookup(brand_scores: dict, brand: str):
    if brand in brand_scores:
        return brand_scores[brand]
    lowered = {(key or "").casefold(): value for key, value in brand_scores.items()}
    return lowered.get((brand or "").casefold())


def ranked_visibility_available(basis: str, *, has_metrics: bool) -> bool:
    """Whether Share of Voice metrics may be shown (including partial, if present)."""
    if not has_metrics:
        return False
    if (basis or "") in {"empty", "NO_DATA"}:
        return False
    return True
