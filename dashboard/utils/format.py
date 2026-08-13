"""Display helpers — never invent numeric values."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Union

Number = Union[int, float, Decimal]


def fmt_ts(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M UTC") + " (naive)"
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def fmt_pct(value: Optional[Number], *, digits: int = 1, already_ratio: bool = True) -> str:
    """Format a ratio (0–1) or percent number. None → No data."""
    if value is None:
        return "No data"
    num = float(value)
    if already_ratio:
        num *= 100.0
    return f"{num:.{digits}f}%"


def fmt_money(value: Optional[Number], currency: Optional[str] = None) -> str:
    if value is None:
        return "No data"
    amount = f"{float(value):,.2f}"
    if currency:
        return f"{currency} {amount}"
    return amount


def fmt_int(value: Optional[int]) -> str:
    if value is None:
        return "No data"
    return f"{value:,}"


def fmt_change(
    current: Optional[Number],
    previous: Optional[Number],
    *,
    as_pct_points: bool = False,
    already_ratio: bool = False,
) -> tuple[Optional[float], str]:
    """Return (delta, label). Insufficient history → (None, 'Insufficient data')."""
    if current is None or previous is None:
        return None, "Insufficient data"
    cur = float(current)
    prev = float(previous)
    if as_pct_points:
        if already_ratio:
            delta = (cur - prev) * 100.0
        else:
            delta = cur - prev
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        return delta, f"{arrow} {abs(delta):.1f} pp vs previous period"
    if prev == 0:
        if cur == 0:
            return 0.0, "→ 0% vs previous period"
        return None, "Insufficient data"
    delta_pct = ((cur - prev) / abs(prev)) * 100.0
    arrow = "↑" if delta_pct > 0 else ("↓" if delta_pct < 0 else "→")
    return delta_pct, f"{arrow} {abs(delta_pct):.1f}% vs previous period"


def safe_ratio(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return numerator / denominator


def metric_or_empty(value: Any, empty_label: str = "No data available") -> Any:
    if value is None:
        return empty_label
    return value
