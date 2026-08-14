"""Collection Status — compact last-run / next-run / component strip."""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Optional

import streamlit as st

from dashboard.queries.collection import CollectionStatusSnapshot, ComponentStatus

_DISPLAY = (
    ("products", "Products"),
    ("audits", "Audits"),
    ("badges", "Badges"),
    ("pricing", "Pricing"),
    ("banners", "Banners"),
    ("search", "Search"),
)


def _kind(status: str) -> str:
    value = (status or "").upper()
    if value in {"SUCCESS", "COMPLETE", "COMPLETED", "OK"}:
        return "ok"
    if value in {"PARTIAL"}:
        return "warn"
    if value in {"FAILED", "ERROR", "BLOCKED"}:
        return "bad"
    return "muted"


def _label(title: str, status: str) -> str:
    value = (status or "").upper()
    if value in {"SUCCESS", "COMPLETE", "COMPLETED", "OK"}:
        return f"✓ {title}"
    if value == "PARTIAL":
        return f"⚠ {title} PARTIAL"
    if value in {"FAILED", "ERROR", "BLOCKED"}:
        return f"✕ {title} FAILED"
    if value == "RUNNING":
        return f"… {title} RUNNING"
    return f"— {title} NOT AVAILABLE"


def _normalize(status: str | None) -> str:
    value = (status or "").upper()
    if value in {"SUCCESS", "COMPLETE", "COMPLETED", "OK"}:
        return "SUCCESS"
    if value in {"PARTIAL", "PARTIAL_SUCCESS"}:
        return "PARTIAL"
    if value in {"FAILED", "ERROR"}:
        return "FAILED"
    if value in {"RUNNING"}:
        return "RUNNING"
    if not value or value in {"NO_DATA", "UNKNOWN", "SKIPPED"}:
        return "NOT AVAILABLE"
    return value


def _component_map(components: list[ComponentStatus]) -> dict[str, ComponentStatus]:
    return {c.component.lower(): c for c in components}


def _aggregate_products(by_name: dict[str, ComponentStatus]) -> str:
    parts = [
        _normalize(by_name[key].status)
        for key in ("newegg", "mercadolibre")
        if key in by_name
    ]
    if not parts:
        return "NOT AVAILABLE"
    if all(p == "SUCCESS" for p in parts):
        return "SUCCESS"
    if all(p == "FAILED" for p in parts):
        return "FAILED"
    if all(p == "NOT AVAILABLE" for p in parts):
        return "NOT AVAILABLE"
    if any(p in {"PARTIAL", "FAILED", "SUCCESS"} for p in parts) and not all(
        p == "SUCCESS" for p in parts
    ):
        if any(p != "NOT AVAILABLE" for p in parts):
            if "FAILED" in parts and "SUCCESS" in parts:
                return "PARTIAL"
            if "PARTIAL" in parts:
                return "PARTIAL"
            if "FAILED" in parts and len(parts) > 1:
                return "PARTIAL"
            if len(set(parts)) == 1:
                return parts[0]
            return "PARTIAL"
    return parts[0]


def _component_status(key: str, by_name: dict[str, ComponentStatus]) -> str:
    if key == "products":
        return _aggregate_products(by_name)
    step = by_name.get(key)
    if step is None:
        return "NOT AVAILABLE"
    return _normalize(step.status)


def _format_when(value: Optional[datetime]) -> str:
    if value is None:
        return "—"
    dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    label = None
    try:
        label = st.session_state.get("display_timezone")
    except Exception:
        label = None
    if label == "UTC":
        local = dt.astimezone(timezone.utc)
        abbrev = "UTC"
    elif label == "India Standard Time":
        local = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
        abbrev = "IST"
    else:
        local = dt.astimezone()
        name = local.tzname() or ""
        if name in {"India Standard Time", "India Daylight Time"} or local.utcoffset() == timedelta(
            hours=5, minutes=30
        ):
            abbrev = "IST"
        else:
            abbrev = name or "local"
    return f"{local.strftime('%d %b %Y')} · {local.strftime('%H:%M')} {abbrev}"


def last_collection_line(collection: CollectionStatusSnapshot) -> str:
    if not collection.latest_run_id:
        return "No collection runs in the database"
    status = _normalize(collection.latest_status)
    when = _format_when(collection.latest_completed_at or collection.latest_started_at)
    return f"{status} · {when}"


def render(collection: CollectionStatusSnapshot) -> None:
    by_name = _component_map(collection.components)
    chips = []
    for key, title in _DISPLAY:
        status = _component_status(key, by_name)
        kind = _kind(status)
        chips.append(
            f'<span class="ci-pill ci-pill-{html.escape(kind)}">'
            f"{html.escape(_label(title, status))}"
            "</span>"
        )
    next_label = collection.next_scheduled_hint or "Configured 08:00 / 14:00 / 20:00"
    st.markdown(
        f"""
        <div class="ci-collection-status">
          <div class="ci-kicker">Collection Status</div>
          <div class="ci-status-grid">
            <div>
              <div class="ci-status-label">Last collection</div>
              <div class="ci-status-value">{html.escape(last_collection_line(collection))}</div>
            </div>
            <div>
              <div class="ci-status-label">Next scheduled collection</div>
              <div class="ci-status-value">{html.escape(next_label)}</div>
            </div>
          </div>
          <div class="ci-status-components">{"".join(chips)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
