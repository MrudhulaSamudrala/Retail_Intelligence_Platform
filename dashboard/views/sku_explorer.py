"""SKU Explorer page."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session

from dashboard.components.header import render_header
from dashboard.components.tables import show_dataframe
from dashboard.config import display_settings
from dashboard.filters import DashboardFilters
from dashboard.queries.catalog import list_sku_rows, product_detail
from dashboard.queries.collection import CollectionStatusSnapshot
from dashboard.utils.format import fmt_ts


def render(
    session: Session,
    filters: DashboardFilters,
    collection: CollectionStatusSnapshot,
    refreshed_at: datetime | None,
) -> None:
    render_header(
        page_title="SKU Explorer",
        subtitle="Product-level exploration over live PostgreSQL catalog and observations.",
        collection=collection,
        filters=filters,
        analytics_refreshed_at=refreshed_at,
    )

    search = st.text_input("Search product / SKU / brand / OEM", value="")
    limit = int(display_settings().get("max_table_rows", 500))
    rows = list_sku_rows(session, filters=filters, search=search, limit=limit)
    df = pd.DataFrame(
        [
            {
                "product_id": r.product_id,
                "product": r.title,
                "sku": r.retailer_sku,
                "brand": r.brand,
                "oem": r.oem,
                "retailer": r.retailer_code,
                "country": r.country_code,
                "product_type": r.product_type,
                "current_price": float(r.current_price) if r.current_price is not None else None,
                "currency": r.currency,
                "discount": float(r.discount_pct) if r.discount_pct is not None else None,
                "last_observed": r.last_observed_at,
                "evidence_status": r.evidence_status or "—",
                "url": r.url,
            }
            for r in rows
        ]
    )
    st.caption(
        "Share of Shelf / Visibility / Compliance Score columns are available in detail panel "
        "(computed from analytics for the selected product)."
    )
    show_dataframe(df, empty_message="No products match the selected filters/search.")

    if df.empty:
        return

    options = [
        f"{r.product_id} | {r.retailer_code} | {r.retailer_sku} | {(r.title or '')[:60]}"
        for r in rows
    ]
    picked = st.selectbox("Select product for detail panel", options=["(select)"] + options)
    if picked == "(select)":
        return

    product_id = int(picked.split("|", 1)[0].strip())
    detail = product_detail(session, product_id)
    if detail.get("error"):
        st.error(detail["error"])
        return

    product = detail["product"]
    left, right = st.columns([2, 1])
    with left:
        st.markdown(f"### {product.title or product.retailer_sku}")
        st.write(
            {
                "retailer": product.retailer_code,
                "country": product.country_code,
                "sku": product.retailer_sku,
                "brand": product.brand,
                "oem": product.oem,
                "product_type": product.product_type,
                "url": product.canonical_url,
                "last_seen": fmt_ts(product.last_seen_at),
            }
        )
        if product.canonical_url:
            st.markdown(f"[Open retailer product page]({product.canonical_url})")
        if detail.get("evidence"):
            st.write("Evidence / access status:", detail["evidence"])
    with right:
        if detail.get("image_url"):
            st.image(detail["image_url"], use_container_width=True)
        else:
            st.caption("No stored product image in latest snapshot payload.")

    tabs = st.tabs(
        [
            "Price history",
            "Audits",
            "Badges",
            "Visibility",
            "Banners (retailer)",
            "Snapshots / collection",
        ]
    )
    with tabs[0]:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "observed_at": p.observed_at,
                        "price": float(p.price_amount) if p.price_amount is not None else None,
                        "list_price": float(p.list_price) if p.list_price is not None else None,
                        "discount_pct": float(p.discount_pct) if p.discount_pct is not None else None,
                        "currency": p.currency,
                        "on_promo": p.is_on_promotion,
                    }
                    for p in detail["prices"]
                ]
            ),
            empty_message="No price history.",
        )
    with tabs[1]:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "check": a.check_code,
                        "result": a.result,
                        "evidence": a.evidence_text,
                        "reason": (a.details or {}).get("reason") if isinstance(a.details, dict) else None,
                        "observed_at": a.observed_at,
                    }
                    for a in detail["audits"]
                ]
            ),
            empty_message="No audit results.",
        )
    with tabs[2]:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "badge_code": b.badge_code,
                        "badge_text": b.badge_text,
                        "is_relevant": b.is_relevant,
                        "notes": b.relevance_notes,
                        "observed_at": b.observed_at,
                    }
                    for b in detail["badges"]
                ]
            ),
            empty_message="No badge observations.",
        )
    with tabs[3]:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "keyword": s.keyword,
                        "position": s.position,
                        "sponsored": s.is_sponsored,
                        "status": s.collection_status,
                        "observed_at": s.observed_at,
                    }
                    for s in detail["searches"]
                ]
            ),
            empty_message="No search visibility observations for this SKU.",
        )
    with tabs[4]:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "brand_detected": b.brand_detected,
                        "headline": b.headline_text,
                        "observed_at": b.observed_at,
                        "page_type": getattr(b, "page_type", None),
                    }
                    for b in detail["banners"]
                ]
            ),
            empty_message="No banner observations for this retailer/country.",
        )
    with tabs[5]:
        show_dataframe(
            pd.DataFrame(
                [
                    {
                        "observed_at": s.observed_at,
                        "title": s.title,
                        "price": float(s.price_amount) if s.price_amount is not None else None,
                        "currency": s.currency,
                        "run_id": s.collection_run_id,
                        "evidence": (
                            (s.raw_payload or {}).get("evidence")
                            if isinstance(s.raw_payload, dict)
                            else None
                        ),
                    }
                    for s in detail["snapshots"]
                ]
            ),
            empty_message="No snapshots.",
        )
