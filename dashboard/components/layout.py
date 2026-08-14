"""Section headings, status pills, and empty states."""

from __future__ import annotations

import html

import streamlit as st


def section_header(title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="ci-section-head">
            <h2>{html.escape(title)}</h2>
            <p>{html.escape(description)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_pill(label: str, *, kind: str = "neutral") -> str:
    safe = html.escape(label)
    return f'<span class="ci-pill ci-pill-{html.escape(kind)}">{safe}</span>'


def render_status_pill(label: str, *, kind: str = "neutral") -> None:
    st.markdown(status_pill(label, kind=kind), unsafe_allow_html=True)


def pill_kind_for_status(status: str) -> str:
    value = (status or "").upper()
    if value in {"COMPLETE", "SUCCESS", "OK", "LIVE"}:
        return "ok"
    if value in {"PARTIAL", "MIXED", "STALE"}:
        return "warn"
    if value in {"FAILED", "ERROR", "BLOCKED"}:
        return "bad"
    if value in {"UNAVAILABLE", "NO_DATA", "UNKNOWN"}:
        return "muted"
    return "neutral"


def empty_state(title: str, explanation: str) -> None:
    st.markdown(
        f"""
        <div class="ci-empty">
            <div class="ci-empty-title">{html.escape(title)}</div>
            <div class="ci-empty-copy">{html.escape(explanation)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def subtle_note(text: str) -> None:
    st.markdown(f'<p class="ci-note">{html.escape(text)}</p>', unsafe_allow_html=True)


def insight_card(text: str, *, label: str = "Insight") -> None:
    st.markdown(
        f"""
        <div class="ci-insight">
            <div class="ci-insight-label">{html.escape(label)}</div>
            <p class="ci-insight-text">{html.escape(text)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_gap() -> None:
    st.markdown("<div class='ci-section-gap'></div>", unsafe_allow_html=True)


def card():
    return st.container(border=True)


def coverage_cards(items: list[dict[str, str]]) -> None:
    if not items:
        return
    cards = []
    for item in items:
        kind = pill_kind_for_status(item.get("status", ""))
        cards.append(
            f"""
            <div class="ci-coverage">
                <div class="ci-coverage-name">{html.escape(item.get("name", ""))}</div>
                <div class="ci-coverage-metric">{html.escape(item.get("headline", ""))}</div>
                {status_pill(item.get("status", ""), kind=kind)}
            </div>
            """
        )
    st.markdown(
        f'<div class="ci-coverage-row">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )
