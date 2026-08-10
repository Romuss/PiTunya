"""Add SuggestedRule table for ML-based rule suggestions.

Revision ID: 029
Revises: 028
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "suggestedrule",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("current_node_id", sa.Integer(), nullable=True),
        sa.Column("suggested_node_id", sa.Integer(), nullable=True),
        sa.Column("suggestion_type", sa.String(), nullable=False, server_default="latency"),
        sa.Column("reason", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_suggestedrule_domain", "suggestedrule", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_suggestedrule_domain", table_name="suggestedrule")
    op.drop_table("suggestedrule")
