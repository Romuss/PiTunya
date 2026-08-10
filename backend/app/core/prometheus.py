"""Prometheus metrics exporter — exposes PiTun metrics in text format.

No dependencies on prometheus_client — we hand-roll the text format
(it's trivial and avoids adding a pip package).

Endpoint: GET /metrics (no auth — Prometheus scrapes, can't send Bearer).
Protection: nftables already gates port 8000 to LAN only.

Metrics groups:
  - System:      CPU/RAM/disk/net (from SystemMetric table, last row)
  - Nodes:       latency, online, speed (from Node table)
  - Traffic:     per-outbound uplink/downlink bytes (xray stats API)
  - DNS:         query count, blocked count (from DNSQueryLog)
  - Devices:     total, online (from Device table)
  - NodeCircle:  rotation count, failover events (from Event table)
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlmodel import select, func

from app.database import get_async_engine
from app.core.stats import get_outbound_stats

logger = logging.getLogger(__name__)

# Age window for aggregate queries (last 24h)
_QUERY_WINDOW = timedelta(hours=24)


async def _fmt(label: str, value: str | int | float | None) -> str:
    """Format a metric line. None → skip."""
    if value is None:
        return ""
    return f"{label} {value}"


async def collect_metrics() -> str:
    """Return a Prometheus text-format metrics string."""
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.models import Node, Device, SystemMetric, DNSQueryLog, Event

    lines: list[str] = []

    # ── Process info ──────────────────────────────────────────────────
    lines.append("# HELP pitun_up PiTun backend is running (1) or not (0).")
    lines.append("# TYPE pitun_up gauge")
    lines.append("pitun_up 1")

    # ── System metrics (latest row) ───────────────────────────────────
    lines.append("# HELP pitun_cpu_percent CPU usage percentage.")
    lines.append("# TYPE pitun_cpu_percent gauge")

    lines.append("# HELP pitun_ram_used_mb RAM used in MB.")
    lines.append("# TYPE pitun_ram_used_mb gauge")

    lines.append("# HELP pitun_ram_total_mb Total RAM in MB.")
    lines.append("# TYPE pitun_ram_total_mb gauge")

    lines.append("# HELP pitun_disk_used_gb Disk used in GB.")
    lines.append("# TYPE pitun_disk_used_gb gauge")

    lines.append("# HELP pitun_disk_total_gb Total disk in GB.")
    lines.append("# TYPE pitun_disk_total_gb gauge")

    lines.append("# HELP pitun_net_sent_bytes Network bytes sent (cumulative).")
    lines.append("# TYPE pitun_net_sent_bytes counter")

    lines.append("# HELP pitun_net_recv_bytes Network bytes received (cumulative).")
    lines.append("# TYPE pitun_net_recv_bytes counter")

    async with AsyncSession(get_async_engine()) as session:
        # Latest system metric
        latest_metric = (await session.exec(
            select(SystemMetric).order_by(SystemMetric.ts.desc()).limit(1)
        )).first()
        if latest_metric:
            lines.append(f"pitun_cpu_percent {latest_metric.cpu_percent}")
            lines.append(f"pitun_ram_used_mb {latest_metric.ram_used_mb}")
            lines.append(f"pitun_ram_total_mb {latest_metric.ram_total_mb}")
            lines.append(f"pitun_disk_used_gb {latest_metric.disk_used_gb}")
            lines.append(f"pitun_disk_total_gb {latest_metric.disk_total_gb}")
            lines.append(f"pitun_net_sent_bytes {latest_metric.net_sent_bytes}")
            lines.append(f"pitun_net_recv_bytes {latest_metric.net_recv_bytes}")

        # ── Node metrics ──────────────────────────────────────────────
        lines.append("# HELP pitun_node_latency_ms Last measured latency in ms (0 if从未 tested).")
        lines.append("# TYPE pitun_node_latency_ms gauge")
        lines.append("# HELP pitun_node_online Node is online (1) or offline (0).")
        lines.append("# TYPE pitun_node_online gauge")
        lines.append("# HELP pitun_node_speed_mbps Last measured speed in MB/s.")
        lines.append("# TYPE pitun_node_speed_mbps gauge")

        nodes = (await session.exec(select(Node))).all()
        for n in nodes:
            lbl = f'node_id="{n.id}",node_name="{_esc(n.name)}"'
            lat = n.latency_ms if n.latency_ms is not None else 0
            lines.append(f"pitun_node_latency_ms{{{lbl}}} {lat}")
            lines.append(f"pitun_node_online{{{lbl}}} {1 if n.is_online else 0}")
            sp = n.speed_mbps if n.speed_mbps is not None else 0
            lines.append(f"pitun_node_speed_mbps{{{lbl}}} {sp}")

        # ── Device metrics ────────────────────────────────────────────
        lines.append("# HELP pitun_devices_total Total registered devices.")
        lines.append("# TYPE pitun_devices_total gauge")
        lines.append("# HELP pitun_devices_online Online devices.")
        lines.append("# TYPE pitun_devices_online gauge")

        dev_total = (await session.exec(
            select(func.count()).select_from(Device)
        )).one()
        dev_online = (await session.exec(
            select(func.count()).select_from(Device).where(Device.is_online == True)  # noqa: E712
        )).one()
        lines.append(f"pitun_devices_total {dev_total}")
        lines.append(f"pitun_devices_online {dev_online}")

        # ── DNS metrics ──────────────────────────────────────────────
        lines.append("# HELP pitun_dns_queries_total Total DNS queries logged (24h window).")
        lines.append("# TYPE pitun_dns_queries_total counter")

        cutoff = datetime.now(tz=timezone.utc) - _QUERY_WINDOW
        dns_count = (await session.exec(
            select(func.count()).select_from(DNSQueryLog).where(DNSQueryLog.timestamp >= cutoff)
        )).one()
        lines.append(f"pitun_dns_queries_total {dns_count}")

        # ── Event metrics ─────────────────────────────────────────────
        lines.append("# HELP pitun_failover_events_total Failover events (24h window).")
        lines.append("# TYPE pitun_failover_events_total counter")
        lines.append("# HELP pitun_circle_rotations_total Circle rotation events (24h window).")
        lines.append("# TYPE pitun_circle_rotations_total counter")

        try:
            from app.models import Event as EventModel
            failover_count = (await session.exec(
                select(func.count()).select_from(EventModel)
                .where(EventModel.category == "failover.via_circle")
                .where(EventModel.created_at >= cutoff)
            )).one()
            rotation_count = (await session.exec(
                select(func.count()).select_from(EventModel)
                .where(EventModel.category == "circle.rotated")
                .where(EventModel.created_at >= cutoff)
            )).one()
            lines.append(f"pitun_failover_events_total {failover_count}")
            lines.append(f"pitun_circle_rotations_total {rotation_count}")
        except Exception:
            pass  # Event model may differ

    # ── xray traffic stats ────────────────────────────────────────────
    lines.append("# HELP pitun_outbound_uplink_bytes Per-outbound uplink bytes.")
    lines.append("# TYPE pitun_outbound_uplink_bytes counter")
    lines.append("# HELP pitun_outbound_downlink_bytes Per-outbound downlink bytes.")
    lines.append("# TYPE pitun_outbound_downlink_bytes counter")

    try:
        stats = await get_outbound_stats()
        for tag, data in stats.items():
            lbl = f'tag="{_esc(tag)}"'
            lines.append(f"pitun_outbound_uplink_bytes{{{lbl}}} {data.get('uplink', 0)}")
            lines.append(f"pitun_outlink_downlink_bytes{{{lbl}}} {data.get('downlink', 0)}")
    except Exception as exc:
        logger.debug("Failed to collect xray stats: %s", exc)

    return "\n".join(lines) + "\n"


def _esc(s: str) -> str:
    """Escape a value for Prometheus label."""
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
