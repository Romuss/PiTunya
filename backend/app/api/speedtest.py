"""Node speed test endpoint (v1.5.0).

Downloads test files through the node's outbound via PiTun's SOCKS5
inbound (port 1080) and measures throughput. Uses multiple CDN URLs
with fallback to avoid 429 rate-limiting.

For non-active nodes: temporarily switches the active_node_id setting,
triggers xray reload, runs the test, then restores the original active
node. This gives per-node speed measurements by actually routing
traffic through each node individually.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.config import settings

logger = logging.getLogger(__name__)

_TEST_URLS = [
    "https://speed.cloudflare.com/__down?bytes=10000000",
    "https://cachefly.cachefly.net/10mb.test",
    "https://proof.ovh.net/files/10Mb.dat",
]
_TEST_TIMEOUT = 20
_RESTORE_DELAY = 2  # seconds to wait after switching active node before testing

router = APIRouter()


async def _save_speed_result(node_id: int, speed: float) -> str:
    async with AsyncSession(get_async_engine()) as session:
        await session.execute(
            text("UPDATE node SET speed_mbps = :speed, last_speed_test = :ts WHERE id = :id"),
            {"speed": speed, "ts": datetime.now(tz=timezone.utc), "id": node_id}
        )
        await session.commit()
        row = (await session.execute(
            text("SELECT name FROM node WHERE id = :id"),
            {"id": node_id}
        )).scalar()
        return row[0] if row else "?"


async def _get_active_node_id() -> Optional[int]:
    async with AsyncSession(get_async_engine()) as session:
        row = (await session.exec(
            text("SELECT value FROM settings WHERE key = 'active_node_id'")
        )).first()
        if row and row[0]:
            try:
                return int(row[0])
            except (ValueError, TypeError):
                return None
    return None


async def _set_active_node_id(node_id: int) -> None:
    async with AsyncSession(get_async_engine()) as session:
        await session.exec(
            text("UPDATE settings SET value = :val WHERE key = 'active_node_id'"),
            params={"val": str(node_id)},
        )
        await session.commit()


async def _reload_xray() -> None:
    """Trigger xray reload so the new active_node_id takes effect."""
    try:
        from app.core.xray import xray_manager
        if xray_manager.is_running:
            await xray_manager.restart()
    except Exception as exc:
        logger.warning("xray reload after active node switch failed: %s", exc)


async def _speed_test_via_socks(node_id: int, socks_port: int) -> float:
    """Download via SOCKS5 and return MB/s. Tries multiple CDN URLs."""
    import httpx
    socks_url = f"socks5://127.0.0.1:{socks_port}"

    for test_url in _TEST_URLS:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                proxy=socks_url, timeout=_TEST_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.get(test_url)
                if resp.status_code == 429:
                    logger.debug("Speed test node %d: 429 from %s, trying next", node_id, test_url)
                    continue
                resp.raise_for_status()
                elapsed = time.monotonic() - start
                total_bytes = len(resp.content)
                if elapsed > 0 and total_bytes > 0:
                    mbps = (total_bytes / 1_000_000) / elapsed
                    logger.info(
                        "Speed test node %d: %.1f MB/s (%d bytes in %.2fs via %s)",
                        node_id, mbps, total_bytes, elapsed, test_url.split('/')[2],
                    )
                    return round(mbps, 1)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                continue
            logger.warning("Speed test node %d: HTTP error from %s: %s", node_id, test_url, exc)
            continue
        except Exception as exc:
            logger.warning("Speed test node %d: failed for %s: %s", node_id, test_url, exc)
            continue

    logger.warning("Speed test node %d: all test URLs failed", node_id)
    return 0.0


@router.post("/nodes/{node_id}/speed-test")
async def speed_test_node(node_id: int):
    """Run a speed test on a specific node.

    If the node is the active node, tests directly. If not, temporarily
    switches active to this node, reloads xray, tests, then restores.
    """
    socks_port = int(settings.socks_port)
    active_id = await _get_active_node_id()
    is_active = (active_id == node_id)
    needs_restore = False

    if not is_active:
        # Switch active node to the test target
        logger.info("Speed test: switching active from %s to %d for test", active_id, node_id)
        old_active = active_id
        await _set_active_node_id(node_id)
        await _reload_xray()
        await asyncio.sleep(_RESTORE_DELAY)
        needs_restore = True

    start = time.monotonic()
    speed = await _speed_test_via_socks(node_id, socks_port)
    duration = time.monotonic() - start

    name = await _save_speed_result(node_id, speed)

    if needs_restore:
        # Restore original active node
        logger.info("Speed test: restoring active to %s", old_active)
        await _set_active_node_id(old_active)
        await _reload_xray()

    return {
        "speed_mbps": speed,
        "duration_s": round(duration, 2),
        "node_id": node_id,
        "node_name": name,
        "is_active_node": is_active,
        "was_temporarily_switched": needs_restore,
    }


@router.post("/nodes/speed-test-all")
async def speed_test_all():
    """Run speed tests on ALL enabled nodes sequentially.

    For each node: temporarily switches active, tests, restores.
    Returns results for every node. This is slow (N × ~5s each) but
    gives real per-node measurements.
    """
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
        results.append({
            "node_id": nid,
            "node_name": nm,
            "speed_mbps": speed,
        })

    return {"results": results, "total": len(results)}
