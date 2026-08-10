"""Add NodeSLARecord + NodeSLADaily tables for SLA tracking.

NodeSLARecord: one row per healthcheck tick (is_online + latency_ms).
NodeSLADaily: daily aggregate (uptime %, avg/max/min latency, downtime events).

Revision ID: 026
Revises: 025
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nodeslarecord",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("is_online", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_nodeslarecord_node_id", "nodeslarecord", ["node_id"])
    op.create_index("ix_nodeslarecord_ts", "nodeslarecord", ["ts"])

    op.create_table(
        "nodesladaily",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("sla_date", sa.Date(), nullable=False),
        sa.Column("uptime_percentage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_checks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_latency_ms", sa.Float(), nullable=True),
        sa.Column("max_latency_ms", sa.Integer(), nullable=True),
        sa.Column("min_latency_ms", sa.Integer(), nullable=True),
        sa.Column("downtime_events", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_downtime_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_nodesladaily_node_id", "nodesladaily", ["node_id"])
    op.create_index("ix_nodesladaily_sla_date", "nodesladaily", ["sla_date"])


def downgrade() -> None:
    op.drop_index("ix_nodesladaily_sla_date", table_name="nodesladaily")
    op.drop_index("ix_nodesladaily_node_id", table_name="nodesladaily")
    op.drop_table("nodesladaily")
    op.drop_index("ix_nodeslarecord_ts", table_name="nodeslarecord")
    op.drop_index("ix_nodeslarecord_node_id", table_name="nodeslarecord")
    op.drop_table("nodeslarecord")
