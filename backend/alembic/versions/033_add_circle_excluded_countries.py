"""Add excluded_countries column to nodecircle.

Per-circle list of ISO-3166-1 alpha-2 country codes whose proxies should be
excluded from rotation (comma-separated, e.g. "DE,NL"). Useful when the
operator has a data-limited LTE proxy in a country and doesn't want the
circle to burn through that quota — candidates whose exit `country` matches
any code here are skipped during rotation AND excluded from auto-sync
when the circle is linked to a subscription.

Default empty string = no exclusions (preserves prior behavior).

Revision ID: 033
Revises: 032
Create Date: 2026-08-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.add_column(
            sa.Column("excluded_countries", sa.String(), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.drop_column("excluded_countries")
