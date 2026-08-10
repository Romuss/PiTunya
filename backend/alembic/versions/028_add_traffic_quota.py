"""Add TrafficQuota + TrafficUsage tables for monthly traffic caps.

TrafficQuota: configured limits per device/routing_set/global.
TrafficUsage: monthly aggregate (bytes per scope per month).

Revision ID: 028
Revises: 027
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trafficquota",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False, server_default="device"),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("monthly_limit_gb", sa.Float(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(), nullable=False, server_default="block"),
        sa.Column("fallback_node_id", sa.Integer(), nullable=True),
        sa.Column("reset_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "trafficusage",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("bytes_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_recv", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("scope_type", "scope_id", "year", "month", name="uq_trafficusage_scope"),
    )


def downgrade() -> None:
    op.drop_table("trafficusage")
    op.drop_table("trafficquota")
