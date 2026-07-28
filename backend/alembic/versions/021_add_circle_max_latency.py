"""Add max_latency_ms column to nodecircle.

Per-circle latency cap for rotation candidates. When > 0, candidates
with latency_ms > max_latency_ms are filtered out during rotation.
Also used by smart rotation: if active node latency > max_latency →
force rotation (don't skip).

Default 0 = no limit.

Revision ID: 021
Revises: 020
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.add_column(sa.Column("max_latency_ms", sa.Integer(), nullable=False, server_default="0"))

def downgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.drop_column("max_latency_ms")
