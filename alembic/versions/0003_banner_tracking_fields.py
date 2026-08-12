"""Extend banner_observations with homepage tracking evidence fields.

Revision ID: 0003_banner_tracking_fields
Revises: 0002_snapshot_pricing_fields
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_banner_tracking_fields"
down_revision: Union[str, Sequence[str], None] = "0002_snapshot_pricing_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "banner_observations",
        sa.Column("discount_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "banner_observations",
        sa.Column("badge_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "banner_observations",
        sa.Column(
            "link_present",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "banner_observations",
        sa.Column("evidence_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "banner_observations",
        sa.Column("selector", sa.Text(), nullable=True),
    )
    op.add_column(
        "banner_observations",
        sa.Column("detection_method", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("banner_observations", "detection_method")
    op.drop_column("banner_observations", "selector")
    op.drop_column("banner_observations", "evidence_text")
    op.drop_column("banner_observations", "link_present")
    op.drop_column("banner_observations", "badge_text")
    op.drop_column("banner_observations", "discount_text")
