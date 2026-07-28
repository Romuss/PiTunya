"""Add NodeCircle.subscription_id + min_speed_mbps, Node.speed_mbps + last_speed_test.

Three features backed by one migration:
  * Auto-sync NodeCircle from Subscription (subscription_id FK lets the
    circle auto-track rows added/removed by the subscription's refresh)
  * Smart rotation guard (min_speed_mbps — if the active node is still
    fast enough, skip the rotation tick entirely)
  * Speed test (speed_mbps + last_speed_test on each node, populated by
    POST /api/nodes/{id}/speed-test which downloads through the node's
    outbound via the PIX socks-in inbound + gRPC routing pin)

Default `subscription_id = NULL` and `min_speed_mbps = 0` preserve the
existing behavior of manually managed circles.

Revision ID: 020
Revises: 019
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.add_column(sa.Column("subscription_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("min_speed_mbps", sa.Float(), nullable=False, server_default="0"))

    with op.batch_alter_table("node") as batch:
        batch.add_column(sa.Column("speed_mbps", sa.Float(), nullable=True))
        batch.add_column(sa.Column("last_speed_test", sa.DateTime(), nullable=True))

    op.execute("UPDATE nodecircle SET min_speed_mbps = 0 WHERE min_speed_mbps IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.drop_column("last_speed_test")
        batch.drop_column("speed_mbps")

    with op.batch_alter_table("nodecircle") as batch:
        batch.drop_column("min_speed_mbps")
        batch.drop_column("subscription_id")
