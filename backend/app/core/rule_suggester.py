"""Rule suggester — analyzes DNS query logs and suggests routing rules.

Daily background task: queries DNSQueryLog for the last 7 days, groups by
domain, and for high-frequency domains suggests either:
  - Latency optimization (route via lower-latency node)
  - GeoIP optimization (route via geographically closer node)
  - Load balancing (distribute traffic across nodes)

Suggestions are stored in SuggestedRule and surfaced in the UI.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from collections import Counter

from sqlmodel import select, func, delete

from app.database import get_async_engine
from app.models import DNSQueryLog, Node, SuggestedRule

from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 86400  # 24h
_QUERY_THRESHOLD = 10    # min queries to consider a domain
_MIN_DOMAIN_LENGTH = 4  # skip short/obfuscated domains


class RuleSuggester:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Rule suggester started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        # Initial delay: 10 min after boot
        await asyncio.sleep(600)
        while self._running:
            try:
                await self._analyze()
            except Exception as exc:
                logger.debug("Rule suggester error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _analyze(self) -> None:
        """Analyze DNS logs and create suggestions."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)

        async with AsyncSession(get_async_engine()) as session:
            # Top domains by frequency
            domain_counts = (await session.exec(
                select(
                    DNSQueryLog.domain,
                    func.count(DNSQueryLog.id).label("cnt"),
                )
                .where(DNSQueryLog.timestamp >= cutoff)
                .group_by(DNSQueryLog.domain)
                .order_by(func.count(DNSQueryLog.id).desc())
                .limit(50)
            )).all()

            if not domain_counts:
                return

            # Get all nodes for latency comparison
            nodes = (await session.exec(
                select(Node).where(Node.enabled == True)  # noqa: E712
            )).all()

            if not nodes:
                return

            # Find the best node by latency (for suggestion purposes)
            best_node = min(
                (n for n in nodes if n.latency_ms is not None),
                key=lambda n: n.latency_ms,
                default=None,
            )

            now = datetime.now(tz=timezone.utc)
            new_suggestions = 0

            for row in domain_counts:
                domain = row[0] if not hasattr(row, "domain") else row.domain
                count = row[1] if not hasattr(row, "cnt") else row.cnt

                if count < _QUERY_THRESHOLD or len(domain) < _MIN_DOMAIN_LENGTH:
                    continue

                # Skip if suggestion already exists and is pending
                existing = (await session.exec(
                    select(SuggestedRule).where(
                        SuggestedRule.domain == domain,
                        SuggestedRule.status == "pending",
                    )
                )).first()
                if existing:
                    existing.query_count = count
                    existing.last_seen = now
                    session.add(existing)
                    continue

                # Generate suggestion
                # For now: suggest routing via the lowest-latency node
                # (if the domain is accessed frequently)
                if best_node:
                    session.add(SuggestedRule(
                        domain=domain,
                        query_count=count,
                        last_seen=now,
                        current_node_id=None,  # we don't track which node served it
                        suggested_node_id=best_node.id,
                        suggestion_type="latency",
                        reason=(
                            f"'{domain}' queried {count} times in 7 days. "
                            f"Node '{best_node.name}' has the lowest latency "
                            f"({best_node.latency_ms}ms) — routing through it "
                            f"may improve performance."
                        ),
                        status="pending",
                    ))
                    new_suggestions += 1

            # Expire suggestions older than 7 days that weren't acted on
            expire_cutoff = now - timedelta(days=7)
            await session.exec(
                delete(SuggestedRule).where(
                    SuggestedRule.status == "pending",
                    SuggestedRule.created_at < expire_cutoff,
                )
            )

            await session.commit()
            if new_suggestions:
                logger.info("Rule suggester: created %d new suggestions", new_suggestions)


rule_suggester = RuleSuggester()
