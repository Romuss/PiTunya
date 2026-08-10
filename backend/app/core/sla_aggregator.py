"""SLA aggregator — computes daily uptime aggregates from NodeSLARecord.

Runs once at midnight (checked every 5 min for simplicity). Prunes
NodeSLARecord rows older than 30 days.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from sqlmodel import select, delete, func

from app.database import get_async_engine
from app.models import Node, NodeSLARecord, NodeSLADaily

from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 300  # 5 min — we just check if it's midnight
_RETENTION_DAYS = 30   # prune NodeSLARecord older than this


class SLAAggregator:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_run_date: Optional[date] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("SLA aggregator started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("SLA aggregator error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _tick(self) -> None:
        today = datetime.now(tz=timezone.utc).date()
        if self._last_run_date == today:
            return  # already ran today

        # Aggregate yesterday's data (UTC midnight boundary)
        yesterday = today - timedelta(days=1)
        await self._aggregate_day(yesterday)
        await self._prune()
        self._last_run_date = today

    async def _aggregate_day(self, day: date) -> None:
        """Compute and store the daily SLA aggregate for all nodes."""
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        async with AsyncSession(get_async_engine()) as session:
            # Get all nodes that have SLA records for this day
            node_ids = (await session.exec(
                select(NodeSLARecord.node_id)
                .where(NodeSLARecord.ts >= start, NodeSLARecord.ts < end)
                .distinct()
            )).all()

            if not node_ids:
                return

            for node_id in node_ids:
                records = (await session.exec(
                    select(NodeSLARecord)
                    .where(
                        NodeSLARecord.node_id == node_id,
                        NodeSLARecord.ts >= start,
                        NodeSLARecord.ts < end,
                    )
                    .order_by(NodeSLARecord.ts.asc())
                )).all()

                if not records:
                    continue

                total = len(records)
                failed = sum(1 for r in records if not r.is_online)
                uptime_pct = ((total - failed) / total * 100.0) if total else 0.0

                # Latency stats (only from online checks)
                latencies = [r.latency_ms for r in records if r.is_online and r.latency_ms is not None]
                avg_lat = sum(latencies) / len(latencies) if latencies else None
                max_lat = max(latencies) if latencies else None
                min_lat = min(latencies) if latencies else None

                # Downtime events: transitions online → offline
                downtime_events = 0
                for i in range(1, len(records)):
                    if records[i - 1].is_online and not records[i].is_online:
                        downtime_events += 1

                # Total downtime (rough estimate: failed_checks * avg_interval)
                # This is approximate since check intervals vary
                avg_interval = (end - start).total_seconds() / total if total else 0
                total_downtime = int(failed * avg_interval)

                # Upsert: delete existing + insert (SQLite doesn't have ON CONFLICT)
                existing = (await session.exec(
                    select(NodeSLADaily).where(
                        NodeSLADaily.node_id == node_id,
                        NodeSLADaily.date == day,
                    )
                )).first()
                if existing:
                    existing.uptime_percentage = uptime_pct
                    existing.total_checks = total
                    existing.failed_checks = failed
                    existing.avg_latency_ms = avg_lat
                    existing.max_latency_ms = max_lat
                    existing.min_latency_ms = min_lat
                    existing.downtime_events = downtime_events
                    existing.total_downtime_seconds = total_downtime
                    session.add(existing)
                else:
                    session.add(NodeSLADaily(
                        node_id=node_id,
                        date=day,
                        uptime_percentage=uptime_pct,
                        total_checks=total,
                        failed_checks=failed,
                        avg_latency_ms=avg_lat,
                        max_latency_ms=max_lat,
                        min_latency_ms=min_lat,
                        downtime_events=downtime_events,
                        total_downtime_seconds=total_downtime,
                    ))

            await session.commit()
            logger.info("SLA: aggregated %d node(s) for %s", len(node_ids), day.isoformat())

    async def _prune(self) -> None:
        """Delete NodeSLARecord rows older than _RETENTION_DAYS."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_RETENTION_DAYS)
        async with AsyncSession(get_async_engine()) as session:
            result = await session.exec(
                delete(NodeSLARecord).where(NodeSLARecord.ts < cutoff)
            )
            await session.commit()
            if result.rowcount > 0:
                logger.info("SLA: pruned %d old records", result.rowcount)


sla_aggregator = SLAAggregator()
