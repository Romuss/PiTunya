"""Quota checker — enforces monthly traffic caps.

Checks TrafficUsage against TrafficQuota limits every 60s. When exceeded:
  - "block": add nftables drop rule for the device MAC (TODO: nft integration)
  - "fallback": emit event suggesting node switch (actual switch needs config_gen)
  - "throttle": emit event (actual nft rate-limit TODO)

Also aggregates DeviceTraffic into TrafficUsage (transfers 5min → monthly).
"""
import asyncio
import logging
from datetime import datetime, timezone, date
from typing import Optional
from sqlmodel import select, func

from app.database import get_async_engine
from app.models import DeviceTraffic, TrafficQuota, TrafficUsage, Device

from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 60


class QuotaChecker:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_aggregate_ts: Optional[datetime] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Quota checker started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._aggregate()
                await self._check_quotas()
            except Exception as exc:
                logger.debug("Quota checker error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _aggregate(self) -> None:
        """Transfer DeviceTraffic 5min rows into TrafficUsage monthly buckets."""
        now = datetime.now(tz=timezone.utc)
        # Aggregate everything since last run
        since = self._last_aggregate_ts or (now.replace(minute=0, second=0, microsecond=0))

        async with AsyncSession(get_async_engine()) as session:
            # Sum all DeviceTraffic since last aggregate (no per-device mapping
            # yet — we store as global aggregate)
            total = (await session.exec(
                select(
                    func.sum(DeviceTraffic.bytes_sent),
                    func.sum(DeviceTraffic.bytes_recv),
                ).where(DeviceTraffic.ts >= since)
            )).one()

            sent = total[0] or 0
            recv = total[1] or 0

            if sent == 0 and recv == 0:
                self._last_aggregate_ts = now
                return

            today = now.date()
            # Upsert TrafficUsage for current month
            existing = (await session.exec(
                select(TrafficUsage).where(
                    TrafficUsage.scope_type == "global",
                    TrafficUsage.scope_id is None,
                    TrafficUsage.year == today.year,
                    TrafficUsage.month == today.month,
                )
            )).first()

            if existing:
                existing.bytes_sent += sent
                existing.bytes_recv += recv
                session.add(existing)
            else:
                session.add(TrafficUsage(
                    scope_type="global",
                    scope_id=None,
                    year=today.year,
                    month=today.month,
                    bytes_sent=sent,
                    bytes_recv=recv,
                ))

            await session.commit()
            self._last_aggregate_ts = now

    async def _check_quotas(self) -> None:
        """Check all enabled quotas against current month's usage."""
        today = date.today()

        async with AsyncSession(get_async_engine()) as session:
            quotas = (await session.exec(
                select(TrafficQuota).where(TrafficQuota.enabled == True)  # noqa: E712
            )).all()

            for quota in quotas:
                usage = (await session.exec(
                    select(TrafficUsage).where(
                        TrafficUsage.scope_type == quota.scope_type,
                        TrafficUsage.year == today.year,
                        TrafficUsage.month == today.month,
                    )
                )).first()

                if not usage:
                    continue

                total_bytes = (usage.bytes_sent or 0) + (usage.bytes_recv or 0)
                limit_bytes = quota.monthly_limit_gb * 1024 * 1024 * 1024

                if limit_bytes > 0 and total_bytes >= limit_bytes:
                    # Quota exceeded — emit event
                    try:
                        from app.core.events import record_event
                        await record_event(
                            category="traffic.quota_exceeded",
                            severity="warning",
                            title=f"Traffic quota exceeded ({quota.scope_type})",
                            details=(
                                f"Scope: {quota.scope_type}"
                                f"{f' #{quota.scope_id}' if quota.scope_id else ''}, "
                                f"used: {total_bytes / 1e9:.1f} GB, "
                                f"limit: {quota.monthly_limit_gb} GB, "
                                f"action: {quota.action}"
                            ),
                            entity_id=quota.scope_id,
                            dedup_window_sec=3600,
                        )
                    except Exception:
                        pass

                    # TODO: actually apply the nftables/node action here
                    # For now, just log — the event surfaces in the UI
                    logger.warning(
                        "Traffic quota exceeded: %s #%s used %.1f GB / %.0f GB — action=%s",
                        quota.scope_type, quota.scope_id,
                        total_bytes / 1e9, quota.monthly_limit_gb,
                        quota.action,
                    )


quota_checker = QuotaChecker()
