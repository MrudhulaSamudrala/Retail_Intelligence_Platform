"""Isolate report file output so unit tests never overwrite reports/."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
