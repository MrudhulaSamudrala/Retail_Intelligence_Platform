"""Resolve observation collection_run_id values for a report.

Uses only stored relationships:
- the requested collection_runs.id
- collection_runs.run_metadata.parent_run_id
- collection_run_id / child_collection_run_id / parent_run_id inside
  collection_run_steps.details

Does not infer runs from timestamps or from products.last_collection_run_id.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import CollectionRun, CollectionRunStep

_RUN_ID_KEYS = frozenset(
    {"collection_run_id", "child_collection_run_id", "parent_run_id"}
)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _collect_run_ids(payload: Any, found: set[int]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in _RUN_ID_KEYS:
                parsed = _as_int(value)
                if parsed is not None:
                    found.add(parsed)
            _collect_run_ids(value, found)
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_run_ids(item, found)


def observation_run_ids_for_run(session: Session, run_id: int) -> tuple[int, ...]:
    """Return run IDs whose observation rows belong to ``run_id``'s report."""
    found: set[int] = {int(run_id)}
    steps = session.scalars(
        select(CollectionRunStep).where(CollectionRunStep.collection_run_id == run_id)
    ).all()
    for step in steps:
        _collect_run_ids(step.details, found)

    children = session.scalars(select(CollectionRun)).all()
    for child in children:
        meta = child.run_metadata if isinstance(child.run_metadata, dict) else {}
        parent = _as_int(meta.get("parent_run_id"))
        if parent == int(run_id):
            found.add(int(child.id))
    return tuple(sorted(found))


def list_production_run_ids(session: Session) -> list[int]:
    rows = session.scalars(
        select(CollectionRun.id)
        .where(CollectionRun.run_type == "production")
        .order_by(CollectionRun.started_at.asc(), CollectionRun.id.asc())
    ).all()
    return [int(rid) for rid in rows]


def parse_run_id_list(raw: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        value = int(token)
        if value not in seen:
            seen.add(value)
            ids.append(value)
    if not ids:
        raise ValueError("no run IDs provided")
    return ids
