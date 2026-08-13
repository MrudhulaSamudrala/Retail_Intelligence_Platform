"""Data-quality / semantic display states for dashboard metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class DataState(str, Enum):
    OK = "OK"
    ZERO = "ZERO"
    NO_DATA = "NO_DATA"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class MetricValue:
    """Typed metric with explicit semantics (never silent zero-fill)."""

    state: DataState
    value: Optional[Any] = None
    display: str = "No data available"
    detail: str = ""
    denominator: Optional[int] = None
    numerator: Optional[int] = None
    source: str = ""
    definition: str = ""

    @classmethod
    def from_number(
        cls,
        value: Optional[float | int],
        *,
        display: Optional[str] = None,
        zero_is_valid: bool = True,
        denominator: Optional[int] = None,
        numerator: Optional[int] = None,
        source: str = "",
        definition: str = "",
        detail: str = "",
    ) -> "MetricValue":
        if value is None:
            return cls(
                state=DataState.NO_DATA,
                value=None,
                display="No data available",
                detail=detail,
                denominator=denominator,
                numerator=numerator,
                source=source,
                definition=definition,
            )
        if value == 0 and zero_is_valid:
            return cls(
                state=DataState.ZERO,
                value=value,
                display=display if display is not None else "0",
                detail=detail,
                denominator=denominator,
                numerator=numerator,
                source=source,
                definition=definition,
            )
        return cls(
            state=DataState.OK,
            value=value,
            display=display if display is not None else str(value),
            detail=detail,
            denominator=denominator,
            numerator=numerator,
            source=source,
            definition=definition,
        )

    @classmethod
    def partial(cls, display: str, *, detail: str = "", source: str = "") -> "MetricValue":
        return cls(
            state=DataState.PARTIAL,
            value=None,
            display=display,
            detail=detail or "Partial data",
            source=source,
        )

    @classmethod
    def blocked(cls, reason: str, *, source: str = "") -> "MetricValue":
        return cls(
            state=DataState.BLOCKED,
            value=None,
            display="BLOCKED",
            detail=reason,
            source=source,
        )

    @classmethod
    def unknown(cls, reason: str = "Evidence not available", *, source: str = "") -> "MetricValue":
        return cls(
            state=DataState.UNKNOWN,
            value=None,
            display="UNKNOWN",
            detail=reason,
            source=source,
        )

    @classmethod
    def insufficient(cls, detail: str = "Insufficient data") -> "MetricValue":
        return cls(state=DataState.INSUFFICIENT, display="Insufficient data", detail=detail)


STATE_LABELS = {
    DataState.OK: "",
    DataState.ZERO: "",
    DataState.NO_DATA: "No data available",
    DataState.UNKNOWN: "UNKNOWN",
    DataState.PARTIAL: "PARTIAL",
    DataState.BLOCKED: "BLOCKED",
    DataState.INSUFFICIENT: "Insufficient data",
}
