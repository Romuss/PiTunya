"""Node speed test endpoint (v1.5.0).

Downloads a fixed-size test file through the node's outbound via
PiTun's SOCKS5 inbound (port 1080) and measures throughput in
megabytes per second. The result is persisted on the Node row as
`speed_mbps` + `last_speed_test` and used by the smart-rotation
logic in circle_scheduler to decide whether to skip rotation.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session, get_async_engine
from app.models import Node
from app.config import settings

logger = logging.getLogger(__name__)

_TEST_URL = "https://speed.cloudflare.com/__down?bytes=10000000"
_TEST_TIMEOUT = 15

router = APIRouter()


async def _speed_test_via_socks(node_id: int, socks_port: int) -> float:
    """Download via SOCKS5 and return MB/s."""
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
):
    """Run a speed test on a specific node.

    Opens its own AsyncSession instead of using Depends(get_session)
    — the dependency-injected session can hit MissingGreenlet when
    the speed test's httpx await overlaps with session update.
    """
    socks_port = int(settings.socks_port)

    # Run the download first (no DB interaction during it)
    start = time.monotonic()
    speed = await _speed_test_via_socks(node_id, socks_port)
    duration = time.monotonic() - start

    # Open a fresh session for the DB update — avoids greenlet issues
    # that arise from mixing httpx awaits with the dependency-injected
    # session's connection lifecycle.
    from sqlmodel.ext.asyncio.session import AsyncSession
    async with AsyncSession(get_async_engine()) as session:
        node = (await session.exec(
            select(Node).where(Node.id == node_id)
        )).first()
        if not node:
            raise HTTPException(404, "Node not found")

        node.speed_mbps = speed
        node.last_speed_test = datetime.now(tz=timezone.utc)
        session.add(node)
        await session.commit()

    return {
        "speed_mbps": speed,
        "duration_s": round(duration, 2),
        "node_id": node_id,
        "node_name": node.name if node else "?",
    }


@router.post("/nodes/speed-test-all")
async def speed_test_all():
    """Run speed tests on all enabled nodes sequentially."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with AsyncSession(get_async_engine()) as session:
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
