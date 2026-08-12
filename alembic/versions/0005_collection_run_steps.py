"""Add collection_run_steps for production orchestration tracking.

Revision ID: 0005_collection_run_steps
Revises: 0004_search_visibility_fields
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_collection_run_steps"
down_revision: Union[str, Sequence[str], None] = "0004_search_visibility_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_run_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("collection_run_id", sa.Integer(), nullable=False),
        sa.Column("component", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection_run_id"],
            ["collection_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_run_steps_run_component",
        "collection_run_steps",
        ["collection_run_id", "component"],
    )
    op.create_index(
        "ix_collection_run_steps_status",
        "collection_run_steps",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_collection_run_steps_status", table_name="collection_run_steps")
    op.drop_index(
        "ix_collection_run_steps_run_component", table_name="collection_run_steps"
    )
    op.drop_table("collection_run_steps")
