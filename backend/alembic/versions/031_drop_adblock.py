"""Drop AdBlockRule + AdBlockList tables — AdBlock feature removed.

Migration 030 created these tables. The AdBlock feature has been
completely removed from the codebase (models, API, config_gen,
frontend). This migration drops the now-orphan tables so the DB
schema matches the ORM models exactly.

Revision ID: 031
Revises: 030
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop AdBlock-specific tables
    op.drop_index("ix_adblockrule_domain_pattern", table_name="adblockrule")
    op.drop_table("adblockrule")
    op.drop_table("adblocklist")

    # Clean up RoutingRule rows that were auto-created by the AdBlock
    # download_list() function. These have name starting with "adblock:"
    # and would otherwise linger as dead domain→block rules.
    op.execute(
        "DELETE FROM routingrule WHERE name LIKE 'adblock:%'"
    )


def downgrade() -> None:
    import sqlalchemy as sa

    op.create_table(
        "adblocklist",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="hosts"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "adblockrule",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("domain_pattern", sa.String(), nullable=False),
        sa.Column("rule_type", sa.String(), nullable=False, server_default="block"),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_adblockrule_domain_pattern", "adblockrule", ["domain_pattern"])
