"""Node speed test endpoint (v1.5.0).

Downloads a fixed-size test file through the node's outbound via
PiTun's SOCKS5 inbound (port 1080) and measures throughput.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.config import settings

logger = logging.getLogger(__name__)

_TEST_URL = "https://speed.cloudflare.com/__down?bytes=10000000"
_TEST_TIMEOUT = 15

router = APIRouter()


async def _speed_test_via_socks(node_id: int, socks_port: int) -> float:
    import httpx
    socks_url = f"socks5://127.0.0.1:{socks_port}"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(proxy=socks_url, timeout=_TEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(_TEST_URL)
            resp.raise_for_status()
            elapsed = time.monotonic() - start
            total_bytes = int(resp.headers.get("content-length", len(resp.content)))
            if elapsed > 0 and total_bytes > 0:
                mbps = (total_bytes / 1_000_000) / elapsed
                logger.info("Speed test node %d: %.1f MB/s", node_id, mbps)
                return round(mbps, 1)
            return 0.0
    except Exception as exc:
        logger.warning("Speed test node %d failed: %s", node_id, exc)
        return 0.0


async def _save_speed_result(node_id: int, speed: float) -> str:
    """Save speed result via raw SQL — avoids all ORM greenlet issues."""
    async with AsyncSession(get_async_engine()) as session:
        result = await session.exec(
            text("UPDATE node SET speed_mbps = :speed, last_speed_test = :ts WHERE id = :id"),
            params={"speed": speed, "ts": datetime.now(tz=timezone.utc), "id": node_id},
        )
        await session.commit()
        # Fetch name via raw SQL too
        row = (await session.exec(
            text("SELECT name FROM node WHERE id = :id"),
            params={"id": node_id},
        )).first()
        return row[0] if row else "?"


@router.post("/nodes/{node_id}/speed-test")
async def speed_test_node(node_id: int):
    """Run a speed test on a specific node."""
    socks_port = int(settings.socks_port)
    start = time.monotonic()
    speed = await _speed_test_via_socks(node_id, socks_port)
    duration = time.monotonic() - start
    name = await _save_speed_result(node_id, speed)
    return {
        "speed_mbps": speed,
        "duration_s": round(duration, 2),
        "node_id": node_id,
        "node_name": name,
    }


@router.post("/nodes/speed-test-all")
async def speed_test_all():
    """Run speed tests on all enabled nodes sequentially."""
    from sqlalchemy import text as sql_text
    async with AsyncSession(get_async_engine()) as session:
        rows = (await session.exec(
            sql_text("SELECT id, name FROM node WHERE enabled = 1 ORDER BY latency_ms ASC NULLS LAST")
        )).all()

    results = []
    for row in rows:
        nid, nm = row[0], row[1]
        speed = await _speed_test_via_socks(nid, int(settings.socks_port))
        await _save_speed_result(nid, speed)
        results.append({"node_id": nid, "node_name": nm, "speed_mbps": speed})

    return {"results": results, "total": len(results)}
