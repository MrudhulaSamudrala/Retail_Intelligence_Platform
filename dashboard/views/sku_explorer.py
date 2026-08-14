"""Product Explorer section."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.filters_ui import select_brand, select_retailer, select_stratum
from dashboard.components.kpi_cards import kpi_card_html
from dashboard.components.layout import card, section_header
from dashboard.components.tables import show_dataframe
from dashboard.filters import DashboardFilters
from dashboard.insights_text import NO_DATA
from dashboard.presentation import CHECK_CODES, CHECK_LABELS, retailer_label
from dashboard.queries.catalog import list_sku_rows, product_detail
from dashboard.queries.collection import CollectionStatusSnapshot, filter_option_values
from dashboard.utils.format import fmt_money, fmt_pct
from dashboard.utils.semantics import MetricValue


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    del collection, refreshed_at
    with card():
        section_header(
            "Product Explorer",
            "Search individual products and trace the data behind the dashboard.",
        )
        options = filter_option_values(session)
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            retailer = select_retailer("sku_retailer", options.get("retailer_code", []))
        with c2:
            brand = select_brand("sku_brand", options.get("brand", []))
        with c3:
            product_type = select_stratum("sku_type", label="Product Type")
        with c4:
            promo = st.selectbox("Promotion", ["All", "On promo", "Not on promo"], key="sku_promo")

        p1, p2, p3 = st.columns([2, 1, 1])
        with p1:
            search = st.text_input("Search product / SKU / model", value="", key="sku_search")
        with p2:
            min_price = st.number_input("Min price", min_value=0.0, value=0.0, step=50.0, key="sku_min_price")
        with p3:
            max_price = st.number_input("Max price", min_value=0.0, value=0.0, step=50.0, key="sku_max_price")

        scoped = replace(
            filters,
            retailer_code=retailer,
            brand=brand,
            product_type=product_type,
            stratum=product_type,
        )
        rows = list_sku_rows(session, filters=scoped, search=search, limit=400)
        if promo == "On promo":
            rows = [r for r in rows if r.is_on_promotion]
        elif promo == "Not on promo":
            rows = [r for r in rows if not r.is_on_promotion]
        if min_price:
            rows = [r for r in rows if r.current_price is not None and float(r.current_price) >= min_price]
        if max_price:
            rows = [r for r in rows if r.current_price is not None and float(r.current_price) <= max_price]

        promo_count = sum(1 for r in rows if r.is_on_promotion)
        summary = [
            ("Products", MetricValue.from_number(len(rows), display=str(len(rows)))),
            ("On Promotion", MetricValue.from_number(promo_count, display=str(promo_count))),
        ]
        inner = "".join(kpi_card_html(label, metric) for label, metric in summary)
        st.markdown(f'<div class="ci-price-kpis" style="grid-template-columns:repeat(2,minmax(0,1fr));max-width:28rem">{inner}</div>', unsafe_allow_html=True)

        table = pd.DataFrame(
            [
                {
                    "product_id": r.product_id,
                    "Product": r.title or r.retailer_sku,
                    "Brand": r.brand or "—",
                    "Type": r.product_type or "—",
                    "Processor": r.processor or "—",
                    "GPU": r.gpu or "—",
                    "RAM": r.ram or "—",
                    "Storage": r.storage or "—",
                    "Price": fmt_money(r.current_price, r.currency) if r.current_price is not None else "N/A",
                    "Promotion": "Yes" if r.is_on_promotion else "No",
                    "Retailer": retailer_label(r.retailer_code),
                }
                for r in rows
            ]
        )
        with st.expander("View products", expanded=False):
            show_dataframe(
                table.drop(columns=["product_id"]) if not table.empty else table,
                empty_message="No products match these filters.",
                empty_explanation=NO_DATA,
                height=320,
            )
        if not rows:
            return

        labels = [
            f"{r.product_id} | {(r.title or r.retailer_sku or '')[:70]}"
            for r in rows[:200]
        ]
        picked = st.selectbox("Select a product", ["(select)"] + labels, key="sku_pick")
        if picked == "(select)":
            return

        product_id = int(picked.split("|", 1)[0].strip())
        detail = product_detail(session, product_id)
        if detail.get("error"):
            st.error(detail["error"])
            return

        product = detail["product"]
        specs = detail.get("specs") or {}
        latest_price = detail["prices"][0] if detail.get("prices") else None
        st.markdown("**Product detail**")
        st.write(
            {
                "Product": product.title or product.retailer_sku,
                "Brand": product.brand,
                "OEM": product.oem,
                "Processor": specs.get("processor") or "—",
                "GPU": specs.get("gpu") or "—",
                "RAM": specs.get("ram") or "—",
                "Storage": specs.get("storage") or "—",
                "Price": fmt_money(latest_price.price_amount, latest_price.currency) if latest_price else "N/A",
                "Discount": fmt_pct(latest_price.discount_pct, already_ratio=False) if latest_price and latest_price.discount_pct is not None else "N/A",
            }
        )

        latest_by_code = {}
        for audit in detail.get("audits") or []:
            if audit.check_code not in latest_by_code:
                latest_by_code[audit.check_code] = audit.result
        compliance_row = {
            code: latest_by_code.get(code) or "N/A"
            for code in CHECK_CODES
        }
        st.markdown("**Compliance**")
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "Check": code,
                        "Name": CHECK_LABELS.get(code, code),
                        "Result": compliance_row[code],
                    }
                    for code in CHECK_CODES
                ]
            ),
            height=260,
        )

        badge_names = sorted(
            {
                (b.badge_text or b.badge_code or "").strip()
                for b in (detail.get("badges") or [])
                if (b.badge_text or b.badge_code)
            }
        )
        st.markdown("**Detected badges**")
        if badge_names:
            st.write(", ".join(badge_names[:20]))
        else:
            st.caption("N/A — no badge evidence for this product.")
