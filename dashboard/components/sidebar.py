"""Sidebar navigation — unused in the single-page dashboard; kept for tests/imports."""

from __future__ import annotations

from dashboard.queries.collection import CollectionStatusSnapshot

PAGES = [
    "Executive Overview",
]


def render_sidebar(collection: CollectionStatusSnapshot) -> str:
    del collection
    return PAGES[0]
