"""Add canonical_products and product_crosswalk for cross-retailer identity.

Revision ID: 0006_canonical_product_identity
Revises: 0005_collection_run_steps
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_canonical_product_identity"
down_revision: Union[str, Sequence[str], None] = "0005_collection_run_steps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canonical_products",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("oem", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=256), nullable=True),
        sa.Column("manufacturer_model", sa.String(length=128), nullable=True),
        sa.Column("normalized_name", sa.Text(), nullable=True),
        sa.Column("product_type", sa.String(length=64), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_canonical_products_manufacturer_model",
        "canonical_products",
        ["manufacturer_model"],
    )
    op.create_index(
        "ix_canonical_products_oem_model",
        "canonical_products",
        ["oem", "manufacturer_model"],
    )

    op.create_table(
        "product_crosswalk",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_product_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column(
            "match_status",
            sa.String(length=32),
            nullable=False,
            doc="MATCHED|POSSIBLE_MATCH|UNMATCHED",
        ),
        sa.Column("match_method", sa.String(length=64), nullable=True),
        sa.Column("match_confidence", sa.Numeric(6, 4), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
            ["canonical_product_id"],
            ["canonical_products.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="uq_product_crosswalk_product_id"),
    )
    op.create_index(
        "ix_product_crosswalk_canonical",
        "product_crosswalk",
        ["canonical_product_id"],
    )
    op.create_index(
        "ix_product_crosswalk_status",
        "product_crosswalk",
        ["match_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_crosswalk_status", table_name="product_crosswalk")
    op.drop_index("ix_product_crosswalk_canonical", table_name="product_crosswalk")
    op.drop_table("product_crosswalk")
    op.drop_index("ix_canonical_products_oem_model", table_name="canonical_products")
    op.drop_index(
        "ix_canonical_products_manufacturer_model", table_name="canonical_products"
    )
    op.drop_table("canonical_products")
