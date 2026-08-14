"""Add optional stratum and observation_source on search_observations.

Revision ID: 0007_search_observation_stratum
Revises: 0006_canonical_product_identity
Create Date: 2026-08-14

Nullable columns distinguish stratified catalog SERP slots from historical
keyword Share-of-Voice rows. Existing rows are left unchanged (NULL source).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_search_observation_stratum"
down_revision: Union[str, Sequence[str], None] = "0006_canonical_product_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "search_observations",
        sa.Column("stratum", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "search_observations",
        sa.Column("observation_source", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_search_observations_source_stratum",
        "search_observations",
        ["observation_source", "stratum"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_search_observations_source_stratum",
        table_name="search_observations",
    )
    op.drop_column("search_observations", "observation_source")
    op.drop_column("search_observations", "stratum")
