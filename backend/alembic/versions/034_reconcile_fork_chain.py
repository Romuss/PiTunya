"""Reconcile fork databases onto the upstream migration chain.

Our fork previously carried its own 018-033 migrations using the same
revision ids as upstream's (the two chains diverged from the same base).
Both chains add the same logical features, but a deployed fork database
was stamped by OUR chain and is missing a few upstream columns, plus it
carries fork-only tables that are being removed (SLA, device traffic,
traffic quotas, suggested rules, adblock remnants).

This migration is fully idempotent — every step checks for the column /
table first, so it is a no-op on databases that already followed the
upstream chain (fresh installs included).

Revision ID: 034
Revises: 033
Create Date: 2026-11-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return column in [c["name"] for c in insp.get_columns(table)]


def _has_table(table: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return insp.has_table(table)


def upgrade() -> None:
    # ── 1. Fork leftovers to drop (SLA / billing / suggestions / adblock).
    #      The adblock settings keys are handled at app level; tables here.
    for table in (
        "nodeslarecord",
        "nodesladaily",
        "devicetraffic",
        "trafficquota",
        "suggestedrule",
        "adblock_rule",
        "adblocklist",
    ):
        if _has_table(table):
            op.drop_table(table)

    # Fork stored the lockout deadline as `locked_until`; upstream uses
    # `lock_until`. Migrate the value, then drop the old column.
    if _has_column("user", "locked_until"):
        if _has_column("user", "lock_until"):
            op.execute(
                "UPDATE user SET lock_until = locked_until "
                "WHERE locked_until IS NOT NULL AND lock_until IS NULL"
            )
            with op.batch_alter_table("user") as batch:
                batch.drop_column("locked_until")
        else:
            with op.batch_alter_table("user") as batch:
                batch.alter_column("locked_until", new_column_name="lock_until")

    # ── 2. Columns a fork database may be missing from the upstream chain.
    #      (Databases already on the upstream chain skip these.)

    # 018/027 — subscription TLS verification toggle.
    if _has_column("subscription", "allow_insecure") and not _has_column(
        "subscription", "allow_insecure_tls"
    ):
        with op.batch_alter_table("subscription") as batch:
            batch.alter_column("allow_insecure", new_column_name="allow_insecure_tls")

    # 019 — node speed / circle filters (fork had these under other
    # revision ids, but double-check the column set).
    if not _has_column("node", "speed_mbps"):
        with op.batch_alter_table("node") as batch:
            batch.add_column(sa.Column("speed_mbps", sa.Float(), nullable=True))
    if not _has_column("node", "speed_tested_at"):
        with op.batch_alter_table("node") as batch:
            batch.add_column(sa.Column("speed_tested_at", sa.DateTime(), nullable=True))
    if not _has_column("node", "speed_max_mbps"):
        with op.batch_alter_table("node") as batch:
            batch.add_column(sa.Column("speed_max_mbps", sa.Float(), nullable=True))

    # 023 — NodeCircle.subscription_id.
    if not _has_column("nodecircle", "subscription_id"):
        with op.batch_alter_table("nodecircle") as batch:
            batch.add_column(sa.Column("subscription_id", sa.Integer(), nullable=True))

    # 024/025 — device DHCP reservation.
    if not _has_column("device", "dhcp_reserved_ip"):
        with op.batch_alter_table("device") as batch:
            batch.add_column(sa.Column("dhcp_reserved_ip", sa.String(), nullable=True))

    # 026 — node exit identity.
    for col, coltype in (
        ("country", sa.String(length=2)),
        ("exit_ip", sa.String()),
        ("exit_checked_at", sa.DateTime()),
    ):
        if not _has_column("node", col):
            with op.batch_alter_table("node") as batch:
                batch.add_column(sa.Column(col, coltype(), nullable=True))

    # 028 — cert pinning.
    if not _has_column("node", "pinned_cert_sha256"):
        with op.batch_alter_table("node") as batch:
            batch.add_column(sa.Column("pinned_cert_sha256", sa.String(), nullable=True))

    # 030 — circle exclusions.
    if not _has_column("nodecircle", "excluded_node_ids"):
        with op.batch_alter_table("nodecircle") as batch:
            batch.add_column(
                sa.Column("excluded_node_ids", sa.String(), nullable=False, server_default="[]")
            )
    if not _has_column("nodecircle", "excluded_countries"):
        with op.batch_alter_table("nodecircle") as batch:
            batch.add_column(
                sa.Column("excluded_countries", sa.String(), nullable=False, server_default="[]")
            )

    # 032 — Hysteria2 port hopping.
    if not _has_column("node", "hy2_hop_ports"):
        with op.batch_alter_table("node") as batch:
            batch.add_column(sa.Column("hy2_hop_ports", sa.String(), nullable=True))
    if not _has_column("node", "hy2_hop_interval"):
        with op.batch_alter_table("node") as batch:
            batch.add_column(sa.Column("hy2_hop_interval", sa.String(), nullable=True))

    # 033 — circle multi-subscription list.
    if not _has_column("nodecircle", "subscription_ids"):
        with op.batch_alter_table("nodecircle") as batch:
            batch.add_column(
                sa.Column("subscription_ids", sa.String(), nullable=False, server_default="[]")
            )
        # Seed from the legacy single-subscription link if present.
        op.execute(
            "UPDATE nodecircle SET subscription_ids = "
            "CASE WHEN subscription_id IS NULL THEN '[]' "
            "ELSE '[' || subscription_id || ']' END "
            "WHERE subscription_ids = '[]'"
        )

    # Fork renamed speed_tested_at → last_speed_test and back; make sure
    # no orphan remains (no-op when the column is already gone).
    if _has_column("node", "last_speed_test"):
        if not _has_column("node", "speed_tested_at"):
            op.execute("ALTER TABLE node RENAME COLUMN last_speed_test TO speed_tested_at")
        else:
            with op.batch_alter_table("node") as batch:
                batch.drop_column("last_speed_test")


def downgrade() -> None:
    # Intentionally a no-op: this migration only reconciles schemas and
    # removes tables that the fork no longer ships.
    pass