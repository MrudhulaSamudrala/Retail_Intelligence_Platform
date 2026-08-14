"""Collection report generation from existing database observations."""

from __future__ import annotations

__all__ = [
    "ReportGenerationResult",
    "generate_reports_for_latest",
    "generate_reports_for_run",
]


def __getattr__(name: str):
    if name in __all__:
        from reporting.generate import (
            ReportGenerationResult,
            generate_reports_for_latest,
            generate_reports_for_run,
        )

        mapping = {
            "ReportGenerationResult": ReportGenerationResult,
            "generate_reports_for_latest": generate_reports_for_latest,
            "generate_reports_for_run": generate_reports_for_run,
        }
        return mapping[name]
    raise AttributeError(name)
