"""Traffic API — per-device/per-aggregate bandwidth usage."""
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import DeviceTraffic, Device

router = APIRouter(prefix="/traffic", tags=["traffic"])


class TrafficPoint(BaseModel):
    ts: datetime
    bytes_sent: int
    bytes_recv: int


class DeviceTrafficSummary(BaseModel):
    device_id: Optional[int] = None
    device_name: str
    total_bytes_sent: int
    total_bytes_recv: int
    point_count: int


@router.get("/summary", response_model=List[DeviceTrafficSummary])
async def traffic_summary(
    hours: int = Query(24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    """Total bandwidth usage per device for the last N hours."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

    rows = (await session.exec(
        select(
            DeviceTraffic.device_id,
            func.sum(DeviceTraffic.bytes_sent).label("total_sent"),
            func.sum(DeviceTraffic.bytes_recv).label("total_recv"),
            func.count(DeviceTraffic.id).label("points"),
        )
        .where(DeviceTraffic.ts >= cutoff)
        .group_by(DeviceTraffic.device_id)
    )).all()

    # Resolve device names
    devices = (await session.exec(select(Device))).all()
    name_by_id = {d.id: d.name or d.hostname or d.mac[:8] for d in devices}

    result = []
    for row in rows:
        dev_id = row[0] if not hasattr(row, "device_id") else row.device_id
        total_sent = row[1] if not hasattr(row, "total_sent") else row.total_sent
        total_recv = row[2] if not hasattr(row, "total_recv") else row.total_recv
        points = row[3] if not hasattr(row, "points") else row.points

        name = name_by_id.get(dev_id, "Aggregate") if dev_id else "Aggregate"
        result.append(DeviceTrafficSummary(
            device_id=dev_id,
            device_name=name,
            total_bytes_sent=total_sent or 0,
            total_bytes_recv=total_recv or 0,
            point_count=points,
        ))
    return result


@router.get("/device/{device_id}", response_model=List[TrafficPoint])
async def device_traffic(
    device_id: int,
    hours: int = Query(24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    """Time-series traffic for a specific device."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    rows = (await session.exec(
        select(DeviceTraffic)
        .where(
            DeviceTraffic.device_id == device_id,
            DeviceTraffic.ts >= cutoff,
        )
        .order_by(DeviceTraffic.ts.asc())
    )).all()

    return [
        TrafficPoint(ts=r.ts, bytes_sent=r.bytes_sent, bytes_recv=r.bytes_recv)
        for r in rows
    ]


@router.get("/aggregate", response_model=List[TrafficPoint])
async def aggregate_traffic(
    hours: int = Query(24, ge=1, le=168),
    session: AsyncSession = Depends(get_session),
):
    """Total traffic across all devices (time series)."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    rows = (await session.exec(
        select(
            DeviceTraffic.ts,
            func.sum(DeviceTraffic.bytes_sent).label("sent"),
            func.sum(DeviceTraffic.bytes_recv).label("recv"),
        )
        .where(DeviceTraffic.ts >= cutoff)
        .group_by(DeviceTraffic.ts)
        .order_by(DeviceTraffic.ts.asc())
    )).all()

    return [
        TrafficPoint(
            ts=row[0] if not hasattr(row, "ts") else row.ts,
            bytes_sent=row[1] if not hasattr(row, "sent") else row.sent,
            bytes_recv=row[2] if not hasattr(row, "recv") else row.recv,
        )
        for row in rows
    ]
