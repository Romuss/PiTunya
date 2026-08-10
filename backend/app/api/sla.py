"""SLA (uptime) API — per-node daily uptime aggregates."""
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import NodeSLADaily, Node

router = APIRouter(prefix="/sla", tags=["sla"])


class SLADailyRead(BaseModel):
    node_id: int
    node_name: str
    date: "date"
    uptime_percentage: float
    total_checks: int
    failed_checks: int
    avg_latency_ms: Optional[float] = None
    max_latency_ms: Optional[int] = None
    min_latency_ms: Optional[int] = None
    downtime_events: int
    total_downtime_seconds: int


class SLASummary(BaseModel):
    node_id: int
    node_name: str
    uptime_7d: float
    uptime_30d: float
    avg_latency_7d: Optional[float] = None
    last_check: Optional[date] = None


@router.get("/summary", response_model=List[SLASummary])
async def sla_summary(session: AsyncSession = Depends(get_session)):
    """Uptime summary for all nodes (7d + 30d windows)."""
    today = date.today()
    d7 = today - timedelta(days=7)
    d30 = today - timedelta(days=30)

    nodes = (await session.exec(select(Node))).all()
    name_by_id = {n.id: n.name for n in nodes}

    records = (await session.exec(
        select(NodeSLADaily).where(NodeSLADaily.sla_date >= d30)
    )).all()

    # Group by node_id
    by_node: dict[int, list[NodeSLADaily]] = {}
    for r in records:
        by_node.setdefault(r.node_id, []).append(r)

    result = []
    for node in nodes:
        recs = by_node.get(node.id, [])
        r7 = [r for r in recs if r.sla_date >= d7]
        r30 = recs  # all are >= d30

        def _uptime(items: list[NodeSLADaily]) -> float:
            total = sum(i.total_checks for i in items)
            failed = sum(i.failed_checks for i in items)
            return ((total - failed) / total * 100.0) if total else 100.0

        def _avg_lat(items: list[NodeSLADaily]) -> Optional[float]:
            lats = [i.avg_latency_ms for i in items if i.avg_latency_ms is not None]
            return sum(lats) / len(lats) if lats else None

        last = max((r.sla_date for r in recs), default=None)

        result.append(SLASummary(
            node_id=node.id,
            node_name=name_by_id.get(node.id, f"node-{node.id}"),
            uptime_7d=_uptime(r7),
            uptime_30d=_uptime(r30),
            avg_latency_7d=_avg_lat(r7),
            last_check=last,
        ))
    return result


@router.get("/nodes/{node_id}", response_model=List[SLADailyRead])
async def node_sla(
    node_id: int,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Daily SLA history for a single node."""
    cutoff = date.today() - timedelta(days=days)
    records = (await session.exec(
        select(NodeSLADaily)
        .where(NodeSLADaily.node_id == node_id, NodeSLADaily.sla_date >= cutoff)
        .order_by(NodeSLADaily.sla_date.asc())
    )).all()

    node = await session.get(Node, node_id)
    node_name = node.name if node else f"node-{node_id}"

    return [
        SLADailyRead(
            node_id=r.node_id,
            node_name=node_name,
            date=r.sla_date,
            uptime_percentage=r.uptime_percentage,
            total_checks=r.total_checks,
            failed_checks=r.failed_checks,
            avg_latency_ms=r.avg_latency_ms,
            max_latency_ms=r.max_latency_ms,
            min_latency_ms=r.min_latency_ms,
            downtime_events=r.downtime_events,
            total_downtime_seconds=r.total_downtime_seconds,
        )
        for r in records
    ]
