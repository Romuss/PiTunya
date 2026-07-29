"""Background speed-test scheduler — auto-tests all enabled nodes periodically.

Runs as a background loop (started in main.py lifespan, same pattern
as HealthChecker / SubscriptionScheduler). Every `speed_test_interval`
seconds (default 3600 = 1 hour), iterates all enabled nodes and runs
the per-node speed test (switches active → download → restore) for
each one sequentially.

The results populate `Node.speed_mbps` + `Node.last_speed_test` which
the NodeCircle "best" mode uses to rank candidates. Without this
scheduler, speed_mbps stays NULL for nodes that were never manually
speed-tested, and the min_speed_mbps filter excludes them.
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

        logger.info("Speed test scheduler: starting round of all enabled nodes")
        self._last_run = now

        # Import here to avoid circular deps with speedtest module
        from app.api.speedtest import _speed_test_via_socks, _save_speed_result

        # Get all enabled nodes sorted by latency (fastest first)
        async with AsyncSession(get_async_engine()) as session:
            rows = (await session.execute(
                text("SELECT id, name FROM node WHERE enabled = 1 ORDER BY latency_ms ASC NULLS LAST")
            )).all()

        if not rows:
            logger.info("Speed test scheduler: no enabled nodes — skipping")
            return

        socks_port = int(settings.socks_port)
        tested = 0
        failed = 0

        for row in rows:
            if not self._running:
                return  # stopped mid-round
            node_id = row[0]
            node_name = row[1] if len(row) > 1 else f"node-{node_id}"
            try:
                speed = await _speed_test_via_socks(node_id, socks_port)
                await _save_speed_result(node_id, speed)
                tested += 1
                if speed > 0:
                    logger.info("Speed test scheduler: node %d (%s) = %.1f MB/s",
                               node_id, node_name, speed)
                else:
                    failed += 1
                    logger.debug("Speed test scheduler: node %d (%s) = 0 MB/s (failed)",
                                node_id, node_name)
            except Exception as exc:
                failed += 1
                logger.warning("Speed test scheduler: node %d failed: %s", node_id, exc)

        logger.info("Speed test scheduler: round complete — %d tested, %d failed, %d total",
                    tested - failed, failed, tested)


speed_test_scheduler = SpeedTestScheduler()
