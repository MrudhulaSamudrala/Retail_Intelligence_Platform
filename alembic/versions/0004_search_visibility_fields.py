"""Extend search_observations for Share of Voice completeness metadata.

Revision ID: 0004_search_visibility_fields
Revises: 0003_banner_tracking_fields
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_search_visibility_fields"
down_revision: Union[str, Sequence[str], None] = "0003_banner_tracking_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "search_observations",
        sa.Column("evidence_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "search_observations",
        sa.Column("selector", sa.Text(), nullable=True),
    )
    op.add_column(
        "search_observations",
        sa.Column(
            "collection_status",
            sa.String(length=32),
            nullable=False,
            server_default="COMPLETE",
        ),
    )
    op.add_column(
        "search_observations",
        sa.Column("search_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "search_observations",
        sa.Column("pages_collected", sa.Integer(), nullable=True),
    )
    op.add_column(
        "search_observations",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_search_observations_collection_status",
        "search_observations",
        ["collection_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_search_observations_collection_status",
        table_name="search_observations",
    )
    op.drop_column("search_observations", "details")
    op.drop_column("search_observations", "pages_collected")
    op.drop_column("search_observations", "search_url")
    op.drop_column("search_observations", "collection_status")
    op.drop_column("search_observations", "selector")
    op.drop_column("search_observations", "evidence_text")
