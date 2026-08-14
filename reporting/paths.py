"""Report file paths for BridgeAI collection exports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reports_root() -> Path:
    override = (os.getenv("REPORTS_DIR") or "").strip()
    if override:
        path = Path(override)
        return path if path.is_absolute() else ROOT / path
    return ROOT / "reports"


def run_report_dir(observed_at: datetime, *, root: Path | None = None) -> Path:
    base = root or reports_root()
    return base / observed_at.strftime("%Y-%m-%d")


def report_stem(run_id: int, observed_at: datetime) -> str:
    return f"BridgeAI_Report_Run_{run_id}_{observed_at.strftime('%Y-%m-%d')}"


@dataclass(frozen=True)
class ReportPaths:
    directory: Path
    stem: str
    excel: Path
    psv: Path

    @property
    def date_label(self) -> str:
        return self.directory.name


def paths_for_run(
    run_id: int,
    observed_at: datetime,
    *,
    root: Path | None = None,
) -> ReportPaths:
    directory = run_report_dir(observed_at, root=root)
    stem = report_stem(run_id, observed_at)
    return ReportPaths(
        directory=directory,
        stem=stem,
        excel=directory / f"{stem}.xlsx",
        psv=directory / f"{stem}.psv",
    )
