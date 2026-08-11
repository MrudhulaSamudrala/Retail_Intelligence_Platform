"""Initial schema: 9 core competitive-intelligence tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-11

Creates:
- collection_runs
- products
- product_snapshots
- price_history
- promotions
- retailer_audits
- badges
- banner_observations
- search_observations

Observation tables are append-only by application convention (no UPDATE helpers).
Product identity is unique on (retailer_code, country_code, retailer_sku).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("retailer_code", sa.String(length=64), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_collected", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("run_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_runs_retailer_country",
        "collection_runs",
        ["retailer_code", "country_code"],
    )
    op.create_index("ix_collection_runs_started_at", "collection_runs", ["started_at"])
    op.create_index(
        "ix_collection_runs_run_type_status",
        "collection_runs",
        ["run_type", "status"],
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("retailer_code", sa.String(length=64), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("retailer_sku", sa.String(length=128), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("oem", sa.String(length=64), nullable=True),
        sa.Column("product_type", sa.String(length=64), nullable=True),
        sa.Column("category_raw", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["last_collection_run_id"],
            ["collection_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "retailer_code",
            "country_code",
            "retailer_sku",
            name="uq_products_retailer_country_sku",
        ),
    )
    op.create_index("ix_products_brand", "products", ["brand"])
    op.create_index("ix_products_oem", "products", ["oem"])
    op.create_index("ix_products_product_type", "products", ["product_type"])
    op.create_index(
        "ix_products_retailer_country",
        "products",
        ["retailer_code", "country_code"],
    )
    op.create_index("ix_products_last_seen_at", "products", ["last_seen_at"])

    op.create_table(
        "product_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("oem", sa.String(length=64), nullable=True),
        sa.Column("product_type", sa.String(length=64), nullable=True),
        sa.Column("category_raw", sa.Text(), nullable=True),
        sa.Column("availability", sa.String(length=64), nullable=True),
        sa.Column("price_amount", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_snapshots_product_observed",
        "product_snapshots",
        ["product_id", "observed_at"],
    )
    op.create_index("ix_product_snapshots_observed_at", "product_snapshots", ["observed_at"])
    op.create_index(
        "ix_product_snapshots_brand_type",
        "product_snapshots",
        ["brand", "product_type"],
    )

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("price_amount", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("list_price", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("discount_amount", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("discount_pct", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("is_on_promotion", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_price_history_product_observed",
        "price_history",
        ["product_id", "observed_at"],
    )
    op.create_index("ix_price_history_observed_at", "price_history", ["observed_at"])
    op.create_index("ix_price_history_promo", "price_history", ["is_on_promotion"])

    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("promo_type", sa.String(length=64), nullable=True),
        sa.Column("promo_text", sa.Text(), nullable=True),
        sa.Column("promo_code", sa.String(length=128), nullable=True),
        sa.Column("discount_value", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("discount_unit", sa.String(length=16), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_promotions_product_observed",
        "promotions",
        ["product_id", "observed_at"],
    )
    op.create_index("ix_promotions_observed_at", "promotions", ["observed_at"])
    op.create_index("ix_promotions_promo_type", "promotions", ["promo_type"])

    op.create_table(
        "retailer_audits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retailer_code", sa.String(length=64), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("product_type", sa.String(length=64), nullable=True),
        sa.Column("check_code", sa.String(length=8), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retailer_audits_observed_at", "retailer_audits", ["observed_at"])
    op.create_index(
        "ix_retailer_audits_scope_check",
        "retailer_audits",
        ["retailer_code", "country_code", "brand", "check_code", "observed_at"],
    )
    op.create_index("ix_retailer_audits_result", "retailer_audits", ["result"])
    op.create_index(
        "ix_retailer_audits_product_observed",
        "retailer_audits",
        ["product_id", "observed_at"],
    )

    op.create_table(
        "badges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("badge_code", sa.String(length=64), nullable=True),
        sa.Column("badge_text", sa.Text(), nullable=False),
        sa.Column("is_relevant", sa.Boolean(), nullable=True),
        sa.Column("relevance_notes", sa.Text(), nullable=True),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_badges_product_observed", "badges", ["product_id", "observed_at"])
    op.create_index("ix_badges_badge_code", "badges", ["badge_code"])
    op.create_index("ix_badges_is_relevant", "badges", ["is_relevant"])

    op.create_table(
        "banner_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retailer_code", sa.String(length=64), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("page_type", sa.String(length=64), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("banner_position", sa.Integer(), nullable=True),
        sa.Column("brand_detected", sa.String(length=64), nullable=True),
        sa.Column("oem_detected", sa.String(length=64), nullable=True),
        sa.Column("headline_text", sa.Text(), nullable=True),
        sa.Column("destination_url", sa.Text(), nullable=True),
        sa.Column("is_tracked_brand", sa.Boolean(), nullable=False),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_banner_observations_retailer_observed",
        "banner_observations",
        ["retailer_code", "country_code", "observed_at"],
    )
    op.create_index("ix_banner_observations_brand", "banner_observations", ["brand_detected"])
    op.create_index(
        "ix_banner_observations_tracked",
        "banner_observations",
        ["is_tracked_brand"],
    )

    op.create_table(
        "search_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=True),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retailer_code", sa.String(length=64), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("keyword", sa.String(length=256), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("retailer_sku", sa.String(length=128), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("oem", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("is_sponsored", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["collection_run_id"], ["collection_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_observations_keyword_observed",
        "search_observations",
        ["retailer_code", "country_code", "keyword", "observed_at"],
    )
    op.create_index(
        "ix_search_observations_brand_position",
        "search_observations",
        ["brand", "position"],
    )
    op.create_index("ix_search_observations_product", "search_observations", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_search_observations_product", table_name="search_observations")
    op.drop_index("ix_search_observations_brand_position", table_name="search_observations")
    op.drop_index("ix_search_observations_keyword_observed", table_name="search_observations")
    op.drop_table("search_observations")

    op.drop_index("ix_banner_observations_tracked", table_name="banner_observations")
    op.drop_index("ix_banner_observations_brand", table_name="banner_observations")
    op.drop_index("ix_banner_observations_retailer_observed", table_name="banner_observations")
    op.drop_table("banner_observations")

    op.drop_index("ix_badges_is_relevant", table_name="badges")
    op.drop_index("ix_badges_badge_code", table_name="badges")
    op.drop_index("ix_badges_product_observed", table_name="badges")
    op.drop_table("badges")

    op.drop_index("ix_retailer_audits_product_observed", table_name="retailer_audits")
    op.drop_index("ix_retailer_audits_result", table_name="retailer_audits")
    op.drop_index("ix_retailer_audits_scope_check", table_name="retailer_audits")
    op.drop_index("ix_retailer_audits_observed_at", table_name="retailer_audits")
    op.drop_table("retailer_audits")

    op.drop_index("ix_promotions_promo_type", table_name="promotions")
    op.drop_index("ix_promotions_observed_at", table_name="promotions")
    op.drop_index("ix_promotions_product_observed", table_name="promotions")
    op.drop_table("promotions")

    op.drop_index("ix_price_history_promo", table_name="price_history")
    op.drop_index("ix_price_history_observed_at", table_name="price_history")
    op.drop_index("ix_price_history_product_observed", table_name="price_history")
    op.drop_table("price_history")

    op.drop_index("ix_product_snapshots_brand_type", table_name="product_snapshots")
    op.drop_index("ix_product_snapshots_observed_at", table_name="product_snapshots")
    op.drop_index("ix_product_snapshots_product_observed", table_name="product_snapshots")
    op.drop_table("product_snapshots")

    op.drop_index("ix_products_last_seen_at", table_name="products")
    op.drop_index("ix_products_retailer_country", table_name="products")
    op.drop_index("ix_products_product_type", table_name="products")
    op.drop_index("ix_products_oem", table_name="products")
    op.drop_index("ix_products_brand", table_name="products")
    op.drop_table("products")

    op.drop_index("ix_collection_runs_run_type_status", table_name="collection_runs")
    op.drop_index("ix_collection_runs_started_at", table_name="collection_runs")
    op.drop_index("ix_collection_runs_retailer_country", table_name="collection_runs")
    op.drop_table("collection_runs")
