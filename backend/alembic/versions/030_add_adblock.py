"""Add AdBlockRule + AdBlockList tables for DNS-level ad filtering.

Revision ID: 030
Revises: 029
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "adblockrule",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("domain_pattern", sa.String(), nullable=False),
        sa.Column("rule_type", sa.String(), nullable=False, server_default="block"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_adblockrule_domain_pattern", "adblockrule", ["domain_pattern"])

    op.create_table(
        "adblocklist",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="hosts"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("adblocklist")
    op.drop_index("ix_adblockrule_domain_pattern", table_name="adblockrule")
    op.drop_table("adblockrule")
