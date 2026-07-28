"""Add `allow_insecure` column to subscription.

Per-subscription TLS verification toggle (architecture review finding 1.3).
Prior to this change, every subscription fetch used httpx with
`verify=False` unconditionally — meaning any MITM on the path between
PiTun and the subscription panel could present a forged certificate and
capture the operator's panel credentials + node UUIDs (both embedded
in the request headers and response body).

This migration adds `allow_insecure BOOLEAN NOT NULL DEFAULT FALSE`:
  * Existing subscriptions keep the old secure-by-default behavior.
  * Operators can opt-in to insecure-only for specific subscriptions
    (typically those hosted behind a self-signed cert) via the UI/API.
  * The fetch logic in `api/subscriptions.py:_fetch_subscription` now
    passes `verify=not sub.allow_insecure` to httpx instead of the
    blanket `verify=False`.

Default `False` is the **security default we used to silently violate**.
Operators who relied on the previous "always insecure" behaviour must
explicitly opt in.

Revision ID: 018
Revises: 017
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.add_column(
            sa.Column(
                "allow_insecure",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    # Belt-and-braces backfill: SQLite's batch_alter_table sometimes
    # leaves the column NULL for pre-existing rows even with a
    # server_default. Same pattern as migration 016 (rotate_hwid).
    op.execute("UPDATE subscription SET allow_insecure = 0 WHERE allow_insecure IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.drop_column("allow_insecure")
