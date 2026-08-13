"""Table helpers."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import streamlit as st


def show_dataframe(df: pd.DataFrame, *, empty_message: str = "No data available.") -> None:
    if df is None or df.empty:
        st.info(empty_message)
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def records_to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
