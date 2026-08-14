"""Global CSS / visual theme for the CI dashboard."""

from __future__ import annotations

import streamlit as st

CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");

html, body, [class*="css"] {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

:root {
    --ci-bg: #fafafa;
    --ci-card: #ffffff;
    --ci-border: #eaeaea;
    --ci-text: #171717;
    --ci-muted: #737373;
    --ci-faint: #a3a3a3;
    --ci-accent: #0070f3;
    --ci-ok: #0070f3;
    --ci-ok-bg: #f0f7ff;
    --ci-warn: #b45309;
    --ci-warn-bg: #fffbeb;
    --ci-bad: #e11d48;
    --ci-bad-bg: #fff1f2;
    --ci-shadow: 0 1px 2px rgba(0,0,0,0.04);
    --ci-radius: 10px;
}

@media (prefers-color-scheme: dark) {
    .stApp {
        --ci-bg: #0a0a0a;
        --ci-card: #111111;
        --ci-border: #262626;
        --ci-text: #fafafa;
        --ci-muted: #a3a3a3;
        --ci-faint: #737373;
        --ci-accent: #3b82f6;
        --ci-ok-bg: #0b1220;
        --ci-warn-bg: #1c1408;
        --ci-bad-bg: #1f0b10;
        --ci-shadow: 0 1px 2px rgba(0,0,0,0.4);
    }
}

.stApp {
    background: var(--ci-bg);
    color: var(--ci-text);
}

.block-container {
    padding-top: 1.6rem;
    padding-bottom: 4.5rem;
    max-width: 1480px;
    padding-left: 2rem;
    padding-right: 2rem;
}

[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { visibility: hidden; height: 0; }
[data-testid="stSidebar"] { display: none; }
[data-testid="stSidebarCollapsedControl"] { display: none; }
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }

h1, h2, h3, .ci-title {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: -0.03em;
    color: var(--ci-text);
}

.ci-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1.5rem;
    margin-bottom: 1.75rem;
}
.ci-kicker {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ci-faint);
    margin-bottom: 0.35rem;
}
.ci-title {
    font-size: 1.65rem;
    font-weight: 650;
    line-height: 1.2;
    margin: 0;
}
.ci-subtitle {
    margin: 0.4rem 0 0 0;
    color: var(--ci-muted);
    font-size: 0.95rem;
    line-height: 1.5;
    max-width: 40rem;
}

.ci-section-head { margin: 0 0 0.85rem 0; }
.ci-section-head h2 {
    font-size: 1.15rem;
    font-weight: 600;
    letter-spacing: -0.025em;
    margin: 0;
}
.ci-section-head p {
    margin: 0.35rem 0 0 0;
    color: var(--ci-muted);
    font-size: 0.88rem;
    line-height: 1.45;
    max-width: 46rem;
}

.ci-kpi {
    background: var(--ci-card);
    border: 1px solid var(--ci-border);
    border-radius: var(--ci-radius);
    padding: 0.7rem 0.85rem;
    min-height: 84px;
    box-shadow: var(--ci-shadow);
}
.ci-kpi .label {
    font-size: 0.7rem;
    color: var(--ci-muted);
    font-weight: 500;
    letter-spacing: 0.01em;
    margin-bottom: 0.28rem;
}
.ci-kpi .value {
    font-size: 1.18rem;
    font-weight: 650;
    letter-spacing: -0.03em;
    color: var(--ci-text);
    line-height: 1.2;
}
.ci-kpi .value.ci-val-ok { color: #16a34a; }
.ci-kpi .value.ci-val-warn { color: #d97706; }
.ci-kpi .meta {
    color: var(--ci-faint);
    font-size: 0.68rem;
    margin-top: 0.28rem;
}

.ci-pill {
    display: inline-flex;
    align-items: center;
    padding: 0.12rem 0.5rem;
    border-radius: 999px;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    border: 1px solid var(--ci-border);
    color: var(--ci-muted);
    background: transparent;
}
.ci-pill-ok { color: #2563eb; border-color: #bfdbfe; background: var(--ci-ok-bg); }
.ci-pill-warn { color: var(--ci-warn); border-color: #fde68a; background: var(--ci-warn-bg); }
.ci-pill-bad { color: var(--ci-bad); border-color: #fecdd3; background: var(--ci-bad-bg); }
.ci-pill-muted { color: var(--ci-faint); }

.ci-coverage-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.75rem;
    margin: 0.4rem 0 0.5rem 0;
}
@media (max-width: 720px) {
    .ci-coverage-row { grid-template-columns: 1fr; }
    .ci-top { flex-direction: column; }
}
.ci-coverage {
    background: var(--ci-card);
    border: 1px solid var(--ci-border);
    border-radius: var(--ci-radius);
    padding: 0.85rem 1rem;
    box-shadow: var(--ci-shadow);
}
.ci-coverage-name {
    font-size: 0.78rem;
    color: var(--ci-muted);
    font-weight: 500;
    margin-bottom: 0.25rem;
}
.ci-coverage-metric {
    font-size: 1.05rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    margin-bottom: 0.45rem;
    color: var(--ci-text);
}

.ci-empty {
    border: 1px dashed var(--ci-border);
    border-radius: var(--ci-radius);
    padding: 1.5rem 1.25rem;
    background: var(--ci-card);
}
.ci-empty-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--ci-text);
}
.ci-empty-copy {
    margin-top: 0.3rem;
    color: var(--ci-muted);
    font-size: 0.85rem;
    line-height: 1.5;
    max-width: 36rem;
}
.ci-note {
    color: var(--ci-muted);
    font-size: 0.8rem;
    margin: 0.35rem 0 0.75rem 0;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    background: var(--ci-card);
    border: 1px solid var(--ci-border) !important;
    border-radius: 12px !important;
    box-shadow: var(--ci-shadow);
    padding: 1rem 1.15rem 1.15rem 1.15rem;
    margin-bottom: 1.85rem;
    overflow: visible;
}
.ci-section-gap { height: 0.35rem; }
.ci-kpi-row {
    display: grid;
    grid-template-columns: repeat(6, minmax(140px, 1fr));
    gap: 0.75rem;
    margin: 0 0 1.85rem 0;
}
.ci-banner-kpis {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 0.75rem;
    margin: 0.15rem 0 1rem 0;
}
.ci-insight {
    border: 1px solid var(--ci-border);
    border-radius: var(--ci-radius);
    padding: 0.85rem 1rem;
    background: var(--ci-ok-bg);
    margin: 0.15rem 0 0.85rem 0;
}
.ci-insight-label {
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ci-accent);
    margin-bottom: 0.28rem;
}
.ci-insight-text {
    font-size: 0.9rem;
    font-weight: 500;
    color: var(--ci-text);
    line-height: 1.45;
    margin: 0;
}
.ci-weak {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem 1.5rem;
    margin: 0.35rem 0 0.5rem 0;
}
.ci-weak h4 {
    margin: 0 0 0.4rem 0;
    font-size: 0.82rem;
    font-weight: 600;
}
.ci-weak li {
    color: var(--ci-muted);
    font-size: 0.82rem;
    margin: 0.15rem 0;
}
.ci-matrix {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
}
.ci-matrix th, .ci-matrix td {
    border: 1px solid var(--ci-border);
    padding: 0.45rem 0.55rem;
    text-align: center;
}
.ci-matrix th {
    font-weight: 600;
    color: var(--ci-muted);
    background: #fafafa;
}
.ci-matrix td.ci-cell-good { background: #ecfdf5; color: #166534; }
.ci-matrix td.ci-cell-partial { background: #fffbeb; color: #92400e; }
.ci-matrix td.ci-cell-low { background: #fef2f2; color: #991b1b; }
.ci-matrix td.ci-cell-na { background: #f5f5f5; color: #737373; }
.ci-matrix td.ci-cell-brand {
    text-align: left;
    font-weight: 600;
    color: var(--ci-text);
    background: #fff;
}
@media (max-width: 1100px) {
    .ci-kpi-row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .ci-banner-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .ci-weak { grid-template-columns: 1fr; }
}
@media (max-width: 700px) {
    .ci-kpi-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .ci-banner-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
div[data-testid="stDataFrame"] {
    border: 1px solid var(--ci-border);
    border-radius: var(--ci-radius);
    overflow: hidden;
}

.stSelectbox label, .stRadio label {
    font-size: 0.8rem !important;
    color: var(--ci-muted) !important;
    font-weight: 500 !important;
}

hr { border-color: var(--ci-border) !important; margin: 0.5rem 0 1rem 0 !important; }
</style>
"""


def inject_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
