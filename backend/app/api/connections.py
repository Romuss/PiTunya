"""Live connection tracker API — reads conntrack + enriches with GeoIP/Device."""
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Device
from app.core.conntrack import read_conntrack, enrich_connections

router = APIRouter(prefix="/connections", tags=["connections"])


class Connection(BaseModel):
    protocol: str
    state: str
    src_ip: str
    dst_ip: str
    device_name: str = "?"
    device_mac: Optional[str] = None
    country: Optional[str] = None
    service: str = ""
    via_node: str = "unknown"
    bytes: int = 0


@router.get("/snapshot", response_model=List[Connection])
async def connections_snapshot(session: AsyncSession = Depends(get_session)):
    """One-shot current snapshot of active connections."""
    conns = await read_conntrack()

    # Build device IP → info map
    devices = (await session.exec(select(Device).where(Device.ip is not None))).all()  # type: ignore
    device_map = {d.ip: {"name": d.name or d.hostname or d.mac[:8], "mac": d.mac} for d in devices if d.ip}

    enriched = await enrich_connections(conns, device_map)
    return [Connection(**c) for c in enriched]


@router.get("/summary")
async def connections_summary(
    hours: int = 1,
    session: AsyncSession = Depends(get_session),
):
    """Summary: top destinations, protocol distribution. (Placeholder —
    full implementation needs snapshot history which we don't store yet.)"""
    conns = await read_conntrack()
    return {
        "total_active": len(conns),
        "protocols": {p: sum(1 for c in conns if c["protocol"] == p)
                      for p in set(c["protocol"] for c in conns)},
        "states": {s: sum(1 for c in conns if c["state"] == s)
                   for s in set(c["state"] for c in conns)},
    }
