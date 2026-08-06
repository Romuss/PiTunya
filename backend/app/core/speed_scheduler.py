"""Background speed-test scheduler — auto-tests all enabled nodes periodically.

Runs as a background loop (started in main.py lifespan, same pattern
as HealthChecker / SubscriptionScheduler). Every `speed_test_interval`
seconds (default 3600 = 1 hour), runs the per-node speed test for
each node sequentially. Results populate `Node.speed_mbps` used by
NodeCircle "best" mode + min_speed_mbps filter.

The speed test runs THROUGH the current active route (SOCKS5 → 
xray → whatever node is active). It does NOT switch active node
(no interruption to traffic). All nodes share the same active
route's throughput — meaning the result is the speed of the ACTIVE
node, not each individual node. However, this gives a baseline
"throughput through the proxy" measurement every hour.

For true per-node measurements (with active switching), the operator
should use the manual "Speed All" button on the Nodes page, which
switches active per node.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.config import settings

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 60  # how often to check if it's time to run


class SpeedTestScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_run: Optional[datetime] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop(), name="speed-test-scheduler")
            logger.info("Speed test scheduler started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        # Initial delay to not race with startup
        await asyncio.sleep(120)
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Speed test scheduler error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _read_interval(self) -> int:
        """Read speed_test_interval from DB Settings. Default 3600 (1h)."""
        try:
            async with AsyncSession(get_async_engine()) as session:
                row = (await session.execute(
                    text("SELECT value FROM settings WHERE key = 'speed_test_interval'")
                )).scalar()
                return int(row) if row else 3600
        except Exception:
            return 3600

    async def _tick(self) -> None:
        interval = await self._read_interval()
        now = datetime.now(tz=timezone.utc)
        if interval <= 0:
            return  # disabled
        if self._last_run is not None:
            elapsed = (now - self._last_run).total_seconds()
            if elapsed < interval:
                return  # not time yet

        logger.info("Speed test scheduler: starting round (active route only, no switching)")
        self._last_run = now

        # Import here to avoid circular deps. Uses the new core/speedtest.py
        # which measures the active node through the live tunnel (v1.6.0).
        from app.core.speedtest import speedtest_node
        from app.api.nodes import _persist_node_speed

        # Get current active node — speed test runs THROUGH this node
        # Only test the active node (no switching!). The result is the
        # throughput of the active proxy route, not per-node.
        # For per-node testing, use the manual "Speed All" button.
        async with AsyncSession(get_async_engine()) as session:
            row = (await session.execute(
                text("SELECT value FROM settings WHERE key = 'active_node_id'")
            )).scalar()

        if not row or not str(row).strip():
            logger.info("Speed test scheduler: no active node — skipping")
            return

        try:
            active_id = int(row)
        except (ValueError, TypeError):
            return

        # Get the active Node row to feed speedtest_node
        async with AsyncSession(get_async_engine()) as session:
            from app.models import Node
            node = await session.get(Node, active_id)
        if not node:
            logger.info("Speed test scheduler: active node %d not found — skipping", active_id)
            return

        node_name = node.name or f"node-{active_id}"

        logger.info("Speed test scheduler: testing active node %d (%s)", active_id, node_name)
        try:
            result = await speedtest_node(node)
            mbps = result.get("download_mbps")
            max_mbps = result.get("max_mbps")
            await _persist_node_speed(active_id, mbps, max_mbps)
            if mbps is not None:
                logger.info("Speed test scheduler: node %d (%s) = %.1f MB/s", active_id, node_name, mbps)
            else:
                logger.warning("Speed test scheduler: node %d (%s) failed: %s", active_id, node_name, result.get("error"))
        except Exception as exc:
            logger.warning("Speed test scheduler: active node test failed: %s", exc)


speed_test_scheduler = SpeedTestScheduler()
