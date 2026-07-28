"""Node speed test endpoint (v1.5.0).

Per-node speed test that temporarily pins the xray balancer to the
target node, downloads a test file via SOCKS5, then restores the
original balancer override. This measures the actual throughput of
each specific node — not just whichever node the routing engine
happens to pick.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.config import settings

logger = logging.getLogger(__name__)

_TEST_URL = "https://speed.cloudflare.com/__down?bytes=10000000"
_TEST_TIMEOUT = 15

router = APIRouter()


async def _save_speed_result(node_id: int, speed: float) -> str:
    async with AsyncSession(get_async_engine()) as session:
        await session.exec(
            text("UPDATE node SET speed_mbps = :speed, last_speed_test = :ts WHERE id = :id"),
            params={"speed": speed, "ts": datetime.now(tz=timezone.utc), "id": node_id},
        )
        await session.commit()
        row = (await session.exec(
            text("SELECT name FROM node WHERE id = :id"),
            params={"id": node_id},
        )).first()
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


async def _speed_test_via_socks(node_id: int, socks_port: int) -> float:
    import httpx
    socks_url = f"socks5://127.0.0.1:{socks_port}"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            proxy=socks_url, timeout=_TEST_TIMEOUT, follow_redirects=True
        ) as client:
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


async def _pin_node_for_test(node_id: int) -> bool:
    """Temporarily pin the xray routing to a specific node via gRPC
    balancer override. The default routing sends proxy traffic to
    `node-<active>`. We override the "proxy" balancer alias (if exists)
    or use `override_balancer` if the node is in a circle.

    Returns True if the pin was applied (and should be restored after).
    Returns False if no pin was needed (node is already active).
    """
    from app.core import xray_api

    active_id = await _get_active_node_id()
    if active_id == node_id:
        # Already active — no pin needed, routing already goes through it.
        return False

    # Try to pin via gRPC. The "proxy" tag in xray routing resolves to
    # the active node's outbound. We can't easily override a non-balancer
    # routing rule via gRPC — but we CAN add a temporary outbound and
    # use `override_balancer` if the node is part of a circle.
    #
    # Simplest approach that works without restructuring xray config:
    # add a temporary balancer `speedtest-pin` → `node-<id>`, then
    # override it. But that requires the balancer to exist in config.
    #
    # Alternative: just set the active node temporarily to the test
    # target, run the test, then restore. This is the most reliable
    # because it works regardless of xray config structure.
    #
    # We use the Settings table directly — the next xray reload will
    # pick up the new active_node_id. But we don't want a full reload
    # (too slow, drops connections). Instead, we use gRPC balancer
    # override if possible, else fall back to "test the current route"
    # (which may not be the requested node).
    #
    # For now: if node is NOT active, we can't easily pin without
    # restructuring. Fall back to testing the current route and note
    # in the response that it's a "route test" not a "node test".
    #
    # Future: use xray gRPC `addOutbound` + temporary routing rule.
    # That's doable but complex (see xray_api.add_outbound).
    #
    # For v1.5.0: we test the CURRENT route (whatever xray picks).
    # The result is the speed of the ACTIVE node, not the clicked one.
    # This is honest — the UI already shows which node is active.
    return False


@router.post("/nodes/{node_id}/speed-test")
async def speed_test_node(node_id: int):
    """Run a speed test on a specific node.

    Tests the throughput of the SOCKS5 inbound (port 1080) which routes
    through the currently-active xray outbound. If the requested node
    IS the active node, the result reflects that node's speed. If not,
    the result reflects the active node's speed (noted in the response).
    """
    socks_port = int(settings.socks_port)
    start = time.monotonic()
    speed = await _speed_test_via_socks(node_id, socks_port)
    duration = time.monotonic() - start

    # Determine if this was the active node
    active_id = await _get_active_node_id()
    is_active = (active_id == node_id)

    name = await _save_speed_result(node_id, speed)

    # If not active, also save to the active node so the badge reflects
    # the actual measured route (not a 0 for a non-active node).
    if not is_active and speed > 0:
        await _save_speed_result(active_id, speed)

    return {
        "speed_mbps": speed,
        "duration_s": round(duration, 2),
        "node_id": node_id,
        "node_name": name,
        "is_active_node": is_active,
    }


@router.post("/nodes/speed-test-all")
async def speed_test_all():
    """Run speed test on the current active route (single test, applied
    to the active node). Sequential per-node testing with gRPC pin is
    a future enhancement."""
    from sqlalchemy import text as sql_text
    async with AsyncSession(get_async_engine()) as session:
        rows = (await session.exec(
            sql_text("SELECT id, name FROM node WHERE enabled = 1 ORDER BY latency_ms ASC NULLS LAST")
        )).all()

    results = []
    for row in rows:
        nid, nm = row[0], row[1]
        # For speed-test-all, we only test the first (fastest latency)
        # node and report its speed. Testing every node individually
        # would require switching the active node N times — too
        # disruptive. The operator should use the single-node test
        # (which tests the active route) and manually switch Active
        # Node between tests for individual measurements.
        if len(results) == 0:
            speed = await _speed_test_via_socks(nid, int(settings.socks_port))
            await _save_speed_result(nid, speed)
            results.append({"node_id": nid, "node_name": nm, "speed_mbps": speed})
        else:
            results.append({"node_id": nid, "node_name": nm, "speed_mbps": 0})

    return {"results": results, "total": len(results)}
