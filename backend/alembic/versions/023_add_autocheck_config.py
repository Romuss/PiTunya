"""Add autocheckconfig table (background auto-speedtest sweep config).

Ported from upstream DaveBugg/PiTun v1.5.0-beta.1.

Singleton (row id=1, created on demand by the API/scheduler). Holds the
enable flag, sweep interval, and scope (all / subscription / group / nodes)
for the periodic auto-speedtest that keeps node.speed_mbps fresh.

Revision ID: 023
Revises: 022
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "autocheckconfig",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="360"),
        sa.Column("scope_kind", sa.String(), nullable=False, server_default="all"),
        sa.Column("scope_value", sa.String(), nullable=False, server_default=""),
        sa.Column("last_sweep", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("autocheckconfig")
