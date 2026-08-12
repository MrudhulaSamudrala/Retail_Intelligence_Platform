"""Add pricing / promotion fields to product_snapshots.

Revision ID: 0002_snapshot_pricing_fields
Revises: 0001_initial_schema
Create Date: 2026-08-12

Stores current price, original (list) price, discount percentage, promotion text,
and promotion flag on each append-only product snapshot. Historical rows are never
updated by application repositories.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_snapshot_pricing_fields"
down_revision: Union[str, Sequence[str], None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_snapshots",
        sa.Column("list_price", sa.Numeric(precision=14, scale=4), nullable=True),
    )
    op.add_column(
        "product_snapshots",
        sa.Column("discount_pct", sa.Numeric(precision=8, scale=4), nullable=True),
    )
    op.add_column(
        "product_snapshots",
        sa.Column("promo_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "product_snapshots",
        sa.Column(
            "is_on_promotion",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_product_snapshots_promo",
        "product_snapshots",
        ["is_on_promotion"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_snapshots_promo", table_name="product_snapshots")
    op.drop_column("product_snapshots", "is_on_promotion")
    op.drop_column("product_snapshots", "promo_text")
    op.drop_column("product_snapshots", "discount_pct")
    op.drop_column("product_snapshots", "list_price")
