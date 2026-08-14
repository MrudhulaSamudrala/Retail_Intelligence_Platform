"""Compact in-page filter controls."""

from __future__ import annotations

from typing import Optional

import streamlit as st

from dashboard.presentation import RETAILER_LABELS, STRATA, STRATUM_LABELS, retailer_label, stratum_label


def select_retailer(key: str, options: list[str], *, label: str = "Retailer") -> Optional[str]:
    codes = options or list(RETAILER_LABELS.keys())
    labels = ["All"] + [retailer_label(c) for c in codes]
    values: list[Optional[str]] = [None] + list(codes)
    picked = st.selectbox(label, labels, key=key)
    return values[labels.index(picked)]


def select_stratum(key: str, *, label: str = "Stratum") -> Optional[str]:
    labels = ["All"] + [STRATUM_LABELS[s] for s in STRATA]
    values: list[Optional[str]] = [None] + list(STRATA)
    picked = st.selectbox(label, labels, key=key)
    return values[labels.index(picked)]


def select_brand(key: str, options: list[str], *, label: str = "Brand") -> Optional[str]:
    labels = ["All"] + options
    picked = st.selectbox(label, labels, key=key)
    return None if picked == "All" else picked


def select_keyword(key: str, options: list[str], *, label: str = "Keyword") -> Optional[str]:
    if not options:
        return None
    labels = ["All"] + options
    picked = st.selectbox(label, labels, key=key)
    return None if picked == "All" else picked


def select_currency(key: str, currencies: list[str], *, label: str = "Currency") -> Optional[str]:
    if not currencies:
        return None
    if len(currencies) == 1:
        st.selectbox(label, currencies, key=key, disabled=True)
        return currencies[0]
    preferred = "USD" if "USD" in currencies else currencies[0]
    return st.selectbox(label, currencies, index=currencies.index(preferred), key=key)
