"""Connection tracker — reads /proc/net/nf_conntrack and enriches with GeoIP + Device info.

Provides a live view of active connections through PiTun: who → where,
via which node, with country flag enrichment.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# conntrack line format:
# ipv4 2 tcp 6 43200 ESTABLISHED src=192.168.1.5 dst=142.250.80.46 ...
_CONNTRACK_RE = re.compile(
    r"^(?P<family>\S+)\s+\d+\s+(?P<proto>\S+)\s+\d+\s+\d+\s+(?P<state>\S+)\s+"
    r"src=(?P<src>\S+)\s+dst=(?P<dst>\S+)\s+.*?"
    r"(?:bytes=(?P<bytes>\d+))?",
    re.DOTALL,
)


async def read_conntrack() -> list[dict]:
    """Read active connections. Tries /proc/net/nf_conntrack first,
    falls back to `ss` command, then `conntrack -L`.

    Returns a list of connection records.
    """
    # Try /proc/net/nf_conntrack first
    raw = ""
    try:
        with open("/proc/net/nf_conntrack", "r") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError):
        pass

    # Fallback: conntrack command
    if not raw:
        try:
            proc = await asyncio.create_subprocess_exec(
                "conntrack", "-L",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
            raw = stdout.decode(errors="replace")
        except Exception:
            pass

    # Fallback: ss command (shows TCP connections, no conntrack needed)
    if not raw:
        return await _read_ss()

    # Parse conntrack format
    connections = []
    for line in raw.splitlines():
        m = _CONNTRACK_RE.match(line.strip())
        if not m:
            continue
        connections.append({
            "protocol": m.group("proto"),
            "state": m.group("state"),
            "src_ip": m.group("src"),
            "dst_ip": m.group("dst"),
            "bytes": int(m.group("bytes")) if m.group("bytes") else 0,
        })
    return connections


async def _read_ss() -> list[dict]:
    """Fallback: use `ss` to list TCP connections.

    Works without nf_conntrack module — shows active TCP sockets.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ss", "-tunH",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        lines = stdout.decode(errors="replace").splitlines()
    except Exception:
        return []

    connections = []
    for line in lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        # ss output: tcp ESTAB 0 0 192.168.1.5:443 1.2.3.4:443
        proto = "tcp" if parts[0].startswith("tcp") else "udp" if parts[0].startswith("udp") else parts[0]
        state = parts[1] if len(parts) > 1 else "UNKNOWN"
        src_full = parts[4] if len(parts) > 4 else ""
        dst_full = parts[5] if len(parts) > 5 else (parts[3] if len(parts) > 3 else "")

        src_ip = src_full.rsplit(":", 1)[0] if ":" in src_full else src_full
        dst_ip = dst_full.rsplit(":", 1)[0] if ":" in dst_full else dst_full
        dst_port = 0
        if ":" in dst_full:
            try:
                dst_port = int(dst_full.rsplit(":", 1)[1])
            except ValueError:
                pass

        connections.append({
            "protocol": proto,
            "state": state,
            "src_ip": src_ip,
            "dst_ip": dst_ip,
            "dst_port": dst_port,
            "bytes": 0,
        })
    return connections


async def enrich_connections(connections: list[dict], device_map: dict[str, dict]):
    """Enrich connections with device + GeoIP info (in-place).

    `device_map` is {ip: {name, mac, ...}} — typically from the ARP scan.
    """
    # Resolve GeoIP for each dst_ip (batch)
    dst_ips = {c["dst_ip"] for c in connections if c.get("dst_ip")}
    geo_map: dict[str, str] = {}

    if dst_ips:
        try:
            from app.core.geoip_lookup import lookup_country
            for ip in dst_ips:
                country = lookup_country(ip)
                if country:
                    geo_map[ip] = country
        except Exception:
            pass  # GeoIP not available

    # Get active node for context
    active_node_name = "unknown"
    try:
        from app.database import get_async_engine
        from app.models import Settings as DBSettings, Node
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession

        async with AsyncSession(get_async_engine()) as session:
            row = (await session.exec(
                select(DBSettings).where(DBSettings.key == "active_node_id")
            )).first()
            if row and row.value:
                node = await session.get(Node, int(row.value))
                if node:
                    active_node_name = node.name
    except Exception:
        pass

    for c in connections:
        # Device enrichment
        dev = device_map.get(c.get("src_ip", ""))
        c["device_name"] = dev["name"] if dev else c.get("src_ip", "?")
        c["device_mac"] = dev.get("mac") if dev else None

        # GeoIP enrichment
        c["country"] = geo_map.get(c.get("dst_ip", ""))

        # Service name from port (if extractable from conntrack)
        c["service"] = _port_to_service(c.get("dst_port"))

        c["via_node"] = active_node_name

    return connections


def _port_to_service(port: Optional[int]) -> str:
    """Map well-known ports to service names."""
    if port is None:
        return ""
    services = {
        80: "HTTP", 443: "HTTPS", 53: "DNS",
        22: "SSH", 993: "IMAPS", 587: "SMTP",
        853: "DoT", 5222: "XMPP", 5060: "SIP",
    }
    return services.get(port, str(port))
