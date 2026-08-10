"""Traffic collector — polls xray stats API per-inbound and stores deltas.

xray's stats API gives cumulative bytes per outbound tag. We poll every
30 seconds, compute the delta from the last reading, and store it as a
DeviceTraffic row (mapped from inbound tag → RoutingSet → devices).

For per-device granularity, nftables counters would be needed (TODO).
Currently we track per-RoutingSet (which maps to a group of devices).
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

from sqlmodel import select, delete

from app.database import get_async_engine
from app.core.stats import get_outbound_stats
from app.models import DeviceTraffic

from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

_COLLECTION_INTERVAL = 30  # seconds
_RETENTION_DAYS = 7        # prune 5min-table older than this


class TrafficCollector:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_stats: Dict[str, Dict[str, int]] = {}  # tag → {uplink, downlink}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Traffic collector started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._collect()
            except Exception as exc:
                logger.debug("Traffic collector error: %s", exc)
            await asyncio.sleep(_COLLECTION_INTERVAL)

    async def _collect(self) -> None:
        """Poll xray stats, compute deltas, store in DeviceTraffic."""
        stats = await get_outbound_stats()
        if not stats:
            return

        now = datetime.now(tz=timezone.utc)
        rows: list[DeviceTraffic] = []

        for tag, data in stats.items():
            uplink = data.get("uplink", 0)
            downlink = data.get("downlink", 0)
            prev = self._last_stats.get(tag, {"uplink": 0, "downlink": 0})

            delta_sent = max(0, uplink - prev.get("uplink", 0))
            delta_recv = max(0, downlink - prev.get("downlink", 0))

            # Only store if there was any traffic
            if delta_sent > 0 or delta_recv > 0:
                # tag format: "node-{id}" — extract node_id for device mapping
                # For now, store with no device_id (aggregate); per-device
                # tracking would need nftables per-MAC counters
                device_id = None
                if tag.startswith("node-"):
                    try:
                        # We can't directly map tag → device without
                        # knowing which device's traffic went through
                        # which outbound. Store as aggregate for now.
                        pass
                    except (ValueError, IndexError):
                        pass

                rows.append(DeviceTraffic(
                    device_id=device_id,
                    ts=now,
                    bytes_sent=delta_sent,
                    bytes_recv=delta_recv,
                    period="5min",
                ))

            self._last_stats[tag] = {"uplink": uplink, "downlink": downlink}

        if rows:
            async with AsyncSession(get_async_engine()) as session:
                for row in rows:
                    session.add(row)
                await session.commit()
            logger.debug("Traffic: stored %d data points", len(rows))

        # Prune old data (every 100th collection to avoid load)
        if now.second < 30 and now.minute == 0:
            await self._prune()

    async def _prune(self) -> None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_RETENTION_DAYS)
        async with AsyncSession(get_async_engine()) as session:
            result = await session.exec(
                delete(DeviceTraffic).where(DeviceTraffic.ts < cutoff)
            )
            await session.commit()
            if result.rowcount > 0:
                logger.info("Traffic: pruned %d old records", result.rowcount)


traffic_collector = TrafficCollector()
