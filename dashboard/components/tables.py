"""Table helpers."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
import streamlit as st

from dashboard.components.layout import empty_state


def show_dataframe(
    df: pd.DataFrame,
    *,
    empty_message: str = "No data available.",
    empty_explanation: str = "Nothing matches the current collection and filters.",
    column_config: Optional[dict[str, Any]] = None,
    height: Optional[int] = None,
) -> None:
    if df is None or df.empty:
        empty_state(empty_message, empty_explanation)
        return
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        height=height,
    )


def records_to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)
