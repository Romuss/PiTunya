"""Reconcile speed columns with upstream: rename last_speed_test → speed_tested_at.

Our migration 020 added `last_speed_test`; upstream DaveBugg uses
`speed_tested_at`. This migration renames the column to match upstream
code so we can adopt DaveBugg's speedtest.py and autocheck_scheduler.py
without field-name patches. `speed_max_mbps` was added by migration 024.

Revision ID: 025
Revises: 024
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite needs batch_alter_table for column rename; Postgres supports
    # ALTER TABLE RENAME COLUMN natively. batch_alter_table handles both.
    with op.batch_alter_table("node") as batch:
        batch.alter_column(
            "last_speed_test",
            new_column_name="speed_tested_at",
            existing_type=sa.DateTime(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.alter_column(
            "speed_tested_at",
            new_column_name="last_speed_test",
            existing_type=sa.DateTime(),
            nullable=True,
        )
