"""Global CSS / visual theme for the CI dashboard."""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }
    [data-testid="stSidebar"] {
        background: #f7f8fa;
        border-right: 1px solid #e5e7eb;
    }
    .ci-kpi {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 0.9rem 1rem;
        min-height: 118px;
        box-shadow: 0 1px 2px rgba(16,24,40,0.04);
    }
    .ci-kpi .label {
        font-size: 0.78rem;
        color: #667085;
        font-weight: 600;
        letter-spacing: 0.01em;
        margin-bottom: 0.35rem;
    }
    .ci-kpi .value {
        font-size: 1.45rem;
        font-weight: 700;
        color: #101828;
        line-height: 1.2;
    }
    .ci-kpi .delta-pos { color: #039855; font-size: 0.8rem; margin-top: 0.25rem; }
    .ci-kpi .delta-neg { color: #d92d20; font-size: 0.8rem; margin-top: 0.25rem; }
    .ci-kpi .delta-neu { color: #667085; font-size: 0.8rem; margin-top: 0.25rem; }
    .ci-kpi .meta { color: #98a2b3; font-size: 0.7rem; margin-top: 0.35rem; }
    .ci-badge {
        display: inline-block;
        padding: 0.15rem 0.55rem;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 650;
        border: 1px solid transparent;
    }
    .ci-badge-live { background: #ecfdf3; color: #027a48; border-color: #abefc6; }
    .ci-badge-partial { background: #fffaeb; color: #b54708; border-color: #fedf89; }
    .ci-badge-stale { background: #fef3f2; color: #b42318; border-color: #fecdca; }
    .ci-badge-info { background: #eff8ff; color: #175cd3; border-color: #b2ddff; }
    .ci-section-title {
        font-size: 1.05rem;
        font-weight: 700;
        color: #101828;
        margin: 0.4rem 0 0.2rem 0;
    }
    .ci-muted { color: #667085; font-size: 0.85rem; }
    .ci-trace {
        font-size: 0.72rem;
        color: #98a2b3;
        margin-top: 0.25rem;
    }
    .ci-alert {
        border-radius: 10px;
        border: 1px solid #e5e7eb;
        padding: 0.75rem 0.9rem;
        margin-bottom: 0.5rem;
        background: #fff;
    }
    .ci-alert-critical { border-left: 4px solid #d92d20; }
    .ci-alert-warning { border-left: 4px solid #f79009; }
    .ci-alert-info { border-left: 4px solid #1570ef; }
</style>
"""


def inject_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
