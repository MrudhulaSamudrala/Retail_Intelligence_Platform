"""SQLAlchemy ORM models for BridgeAI competitive intelligence.

Design principles:
- Historical observations are append-only (never overwrite prior rows).
- Every observation carries an observed_at timestamp.
- Product identity is retailer-scoped (retailer + country + retailer_sku).
- No fabricated seed data is created by this module.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# PostgreSQL uses JSONB; SQLite (unit tests) falls back to JSON.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Declarative base for all BridgeAI models."""


class CollectionRun(Base):
    """One scheduled or manual collection execution."""

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retailer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    run_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="pricing | audit | banner | search | discovery | combined",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        doc="pending | running | completed | failed | partial",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    items_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    products_touched: Mapped[list["Product"]] = relationship(back_populates="last_run")
    snapshots: Mapped[list["ProductSnapshot"]] = relationship(back_populates="collection_run")
    prices: Mapped[list["PriceHistory"]] = relationship(back_populates="collection_run")
    promotions: Mapped[list["Promotion"]] = relationship(back_populates="collection_run")
    audits: Mapped[list["RetailerAudit"]] = relationship(back_populates="collection_run")
    badges: Mapped[list["Badge"]] = relationship(back_populates="collection_run")
    banners: Mapped[list["BannerObservation"]] = relationship(back_populates="collection_run")
    searches: Mapped[list["SearchObservation"]] = relationship(back_populates="collection_run")

    __table_args__ = (
        Index("ix_collection_runs_retailer_country", "retailer_code", "country_code"),
        Index("ix_collection_runs_started_at", "started_at"),
        Index("ix_collection_runs_run_type_status", "run_type", "status"),
    )


class Product(Base):
    """Canonical retailer-scoped product identity (mutable latest attributes)."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    retailer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    retailer_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="Intel|AMD|Qualcomm|Apple|UNKNOWN"
    )
    oem: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="Dell|HP|Lenovo|Acer|Asus|MSI|Apple|null"
    )
    product_type: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        doc="notebook|desktop|workstation|tablet|cpu|gpu|UNKNOWN",
    )
    category_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    last_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="products_touched")
    snapshots: Mapped[list["ProductSnapshot"]] = relationship(back_populates="product")
    prices: Mapped[list["PriceHistory"]] = relationship(back_populates="product")
    promotions: Mapped[list["Promotion"]] = relationship(back_populates="product")
    audits: Mapped[list["RetailerAudit"]] = relationship(back_populates="product")
    badges: Mapped[list["Badge"]] = relationship(back_populates="product")
    search_hits: Mapped[list["SearchObservation"]] = relationship(back_populates="product")

    __table_args__ = (
        UniqueConstraint(
            "retailer_code",
            "country_code",
            "retailer_sku",
            name="uq_products_retailer_country_sku",
        ),
        Index("ix_products_brand", "brand"),
        Index("ix_products_oem", "oem"),
        Index("ix_products_product_type", "product_type"),
        Index("ix_products_retailer_country", "retailer_code", "country_code"),
        Index("ix_products_last_seen_at", "last_seen_at"),
    )


class ProductSnapshot(Base):
    """Append-only point-in-time product observation."""

    __tablename__ = "product_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    oem: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    category_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    availability: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="in_stock|out_of_stock|limited|unknown"
    )
    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="snapshots")
    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("ix_product_snapshots_product_observed", "product_id", "observed_at"),
        Index("ix_product_snapshots_observed_at", "observed_at"),
        Index("ix_product_snapshots_brand_type", "brand", "product_type"),
    )


class PriceHistory(Base):
    """Append-only price observations in retailer-native currency."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    price_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    list_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    discount_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    discount_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    is_on_promotion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="prices")
    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="prices")

    __table_args__ = (
        Index("ix_price_history_product_observed", "product_id", "observed_at"),
        Index("ix_price_history_observed_at", "observed_at"),
        Index("ix_price_history_promo", "is_on_promotion"),
    )


class Promotion(Base):
    """Append-only promotion / deal observations."""

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    promo_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    promo_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    promo_code: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    discount_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4), nullable=True)
    discount_unit: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, doc="amount|percent|other"
    )
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="promotions")
    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="promotions")

    __table_args__ = (
        Index("ix_promotions_product_observed", "product_id", "observed_at"),
        Index("ix_promotions_observed_at", "observed_at"),
        Index("ix_promotions_promo_type", "promo_type"),
    )


class RetailerAudit(Base):
    """Append-only retailer audit check results (S1, S2, P1–P5)."""

    __tablename__ = "retailer_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        doc="Nullable for search-level checks (S1/S2) without a single SKU.",
    )
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retailer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    check_code: Mapped[str] = mapped_column(
        String(8), nullable=False, doc="S1|S2|P1|P2|P3|P4|P5"
    )
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="PASS|FAIL|UNKNOWN"
    )
    evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Optional[Product]] = relationship(back_populates="audits")
    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="audits")

    __table_args__ = (
        Index("ix_retailer_audits_observed_at", "observed_at"),
        Index(
            "ix_retailer_audits_scope_check",
            "retailer_code",
            "country_code",
            "brand",
            "check_code",
            "observed_at",
        ),
        Index("ix_retailer_audits_result", "result"),
        Index("ix_retailer_audits_product_observed", "product_id", "observed_at"),
    )


class Badge(Base):
    """Append-only badge detection observations."""

    __tablename__ = "badges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    badge_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    badge_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_relevant: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    relevance_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="badges")
    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="badges")

    __table_args__ = (
        Index("ix_badges_product_observed", "product_id", "observed_at"),
        Index("ix_badges_badge_code", "badge_code"),
        Index("ix_badges_is_relevant", "is_relevant"),
    )


class BannerObservation(Base):
    """Append-only homepage / landing banner observations."""

    __tablename__ = "banner_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retailer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    page_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="homepage", doc="homepage|category|campaign"
    )
    page_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    banner_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brand_detected: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    oem_detected: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    headline_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    destination_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_tracked_brand: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    screenshot_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="banners")

    __table_args__ = (
        Index(
            "ix_banner_observations_retailer_observed",
            "retailer_code",
            "country_code",
            "observed_at",
        ),
        Index("ix_banner_observations_brand", "brand_detected"),
        Index("ix_banner_observations_tracked", "is_tracked_brand"),
    )


class SearchObservation(Base):
    """Append-only Share of Voice / search visibility observations."""

    __tablename__ = "search_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collection_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"), nullable=True
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retailer_code: Mapped[str] = mapped_column(String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    keyword: Mapped[str] = mapped_column(String(256), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retailer_sku: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    oem: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_sponsored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    product: Mapped[Optional[Product]] = relationship(back_populates="search_hits")
    collection_run: Mapped[Optional[CollectionRun]] = relationship(back_populates="searches")

    __table_args__ = (
        Index(
            "ix_search_observations_keyword_observed",
            "retailer_code",
            "country_code",
            "keyword",
            "observed_at",
        ),
        Index("ix_search_observations_brand_position", "brand", "position"),
        Index("ix_search_observations_product", "product_id"),
    )
