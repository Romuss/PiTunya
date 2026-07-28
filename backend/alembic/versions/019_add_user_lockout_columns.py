"""Add brute-force-defense columns to user.

`failed_attempts` (INT NOT NULL DEFAULT 0) + `locked_until` (DATETIME NULL).
First version of `app.core.auth` to use these in the login flow.

Architecture review finding 1.2: 5 attempts through 1 hour lockout.
Lockout is enforced in core/auth.get_current_user + api/auth.login —
the cached token in the request still authenticates, only NEW
logins are blocked. Resetting `failed_attempts` happens automatically
on a successful login (auth.py:login normal path: reset to 0 and
clear locked_until). A backend restart does NOT reset the counter.

Revision ID: 019
Revises: 018
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column(
                "failed_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("locked_until", sa.DateTime(), nullable=True)
        )
    # Backfill: existing rows got `failed_attempts = 0` via
    # server_default, but SQLite's batch_alter_table has a history of
    # leaving the column NULL for pre-existing rows. Same belt-and-
    # braces as migration 016 / 018.
    op.execute("UPDATE user SET failed_attempts = 0 WHERE failed_attempts IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_column("locked_until")
        batch.drop_column("failed_attempts")
