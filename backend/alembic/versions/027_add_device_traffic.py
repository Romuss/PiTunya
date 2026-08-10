"""Add DeviceTraffic table for per-device bandwidth tracking.

Records 5-minute aggregates of bytes sent/received per device (MAC-based).
Polled from xray stats API (per-inbound) + nftables counters (per-MAC).

Revision ID: 027
Revises: 026
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "devicetraffic",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("bytes_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_recv", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("period", sa.String(), nullable=False, server_default="5min"),
    )
    op.create_index("ix_devicetraffic_device_id", "devicetraffic", ["device_id"])
    op.create_index("ix_devicetraffic_ts", "devicetraffic", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_devicetraffic_ts", table_name="devicetraffic")
    op.drop_index("ix_devicetraffic_device_id", table_name="devicetraffic")
    op.drop_table("devicetraffic")
