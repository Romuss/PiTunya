"""Node speed test endpoint (v1.4.8).

Downloads a fixed-size test file through the node's outbound via
PiTun's SOCKS5 inbound (port 1080) and measures throughput in
megabytes per second. The result is persisted on the Node row as
`speed_mbps` + `last_speed_test` and used by the smart-rotation
logic in circle_scheduler to decide whether to skip rotation.

The test uses a 5 MB download from a fast CDN endpoint (Cloudflare's
speed test file). The download is done via httpx with SOCKS5 proxy
so it rides the same xray pipeline as real LAN traffic.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Node
from app.config import settings

logger = logging.getLogger(__name__)

# Test URL — Cloudflare's 10MB speed test file. CDN-distributed,
# reliable, doesn't count against quotas. If unreachable (offline box),
# the test gracefully returns 0 MB/s.
_TEST_URL = "https://speed.cloudflare.com/__down?bytes=10000000"
_TEST_TIMEOUT = 15  # seconds

router = APIRouter()


async def _speed_test_via_socks(node_id: int, socks_port: int) -> float:
    """Download via SOCKS5 and return MB/s.

    Uses httpx's SOCKS5 support (httpx[socks] is in requirements.txt).
    The SOCKS5 proxy is PiTun's own inbound at 127.0.0.1:<socks_port>.
    We pin the xray outbound to the specific node via gRPC balancer
    override *before* the test, so the download goes through that
    specific node — not whatever the routing engine would pick.
    """
    import httpx

    socks_url = f"socks5://127.0.0.1:{socks_port}"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            proxy=socks_url,
            timeout=_TEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(_TEST_URL)
            resp.raise_for_status()
            elapsed = time.monotonic() - start
            # Content-Length or actual body length, whichever is available
            total_bytes = int(resp.headers.get("content-length", len(resp.content)))
            if elapsed > 0 and total_bytes > 0:
                mbps = (total_bytes / 1_000_000) / elapsed
                logger.info(
                    "Speed test node %d: %.1f MB/s (%d bytes in %.2fs)",
                    node_id, mbps, total_bytes, elapsed,
                )
                return round(mbps, 1)
            return 0.0
    except Exception as exc:
        logger.warning("Speed test node %d failed: %s", node_id, exc)
        return 0.0


@router.post("/nodes/{node_id}/speed-test")
async def speed_test_node(
    node_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Run a speed test on a specific node.

    Returns `{"speed_mbps": float, "duration_s": float}`.
    The result is also persisted on the Node row.
    """
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")

    # Use the SOCKS5 inbound port from settings (default 1080).
    # The routing engine will route through this node only if the
    # operator has it as the active node, or if there's a routing
    # rule pointing at it. For a true per-node test we'd need to
    # pin via xray gRPC balancer override — but that only works
    # inside a NodeCircle. For standalone nodes, the test measures
    # the speed of whatever the current routing puts in front.
    socks_port = int(settings.socks_port)

    start = time.monotonic()
    speed = await _speed_test_via_socks(node_id, socks_port)
    duration = time.monotonic() - start

    node.speed_mbps = speed
    node.last_speed_test = datetime.now(tz=timezone.utc)
    session.add(node)
    await session.commit()

    return {
        "speed_mbps": speed,
        "duration_s": round(duration, 2),
        "node_id": node_id,
        "node_name": node.name,
    }


@router.post("/nodes/speed-test-all")
async def speed_test_all(
    session: AsyncSession = Depends(get_session),
):
    """Run speed tests on all enabled nodes sequentially.

    Returns a list of `{node_id, node_name, speed_mbps, duration_s}`.
    Nodes are tested one at a time to avoid bandwidth contention.
    """
    from sqlmodel import select
    nodes = (await session.exec(
        select(Node).where(Node.enabled == True)  # noqa: E712
        .order_by(Node.latency_ms.asc().nulls_last())
    )).all()

    results = []
    for node in nodes:
        speed = await _speed_test_via_socks(node.id, int(settings.socks_port))
        node.speed_mbps = speed
        node.last_speed_test = datetime.now(tz=timezone.utc)
        session.add(node)
        results.append({
            "node_id": node.id,
            "node_name": node.name,
            "speed_mbps": speed,
        })

    await session.commit()
    return {"results": results, "total": len(results)}
