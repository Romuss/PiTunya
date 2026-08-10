"""Quota API — traffic cap management."""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session, get_async_engine
from app.models import TrafficQuota, TrafficUsage

router = APIRouter(prefix="/quotas", tags=["quotas"])


class QuotaCreate(BaseModel):
    scope_type: str = "device"
    scope_id: Optional[int] = None
    monthly_limit_gb: float = 0
    action: str = "block"
    fallback_node_id: Optional[int] = None
    reset_day: int = 1
    enabled: bool = True


class QuotaRead(QuotaCreate):
    id: int
    class Config:
        from_attributes = True


class QuotaUsage(BaseModel):
    scope_type: str
    scope_id: Optional[int]
    year: int
    month: int
    bytes_sent: int
    bytes_recv: int
    total_gb: float
    limit_gb: Optional[float] = None
    action: Optional[str] = None


@router.get("", response_model=List[QuotaRead])
async def list_quotas(session: AsyncSession = Depends(get_session)):
    return (await session.exec(select(TrafficQuota))).all()


@router.post("", response_model=QuotaRead, status_code=201)
async def create_quota(data: QuotaCreate, session: AsyncSession = Depends(get_session)):
    q = TrafficQuota(**data.model_dump())
    session.add(q)
    await session.commit()
    await session.refresh(q)
    return q


@router.patch("/{quota_id}", response_model=QuotaRead)
async def update_quota(quota_id: int, data: dict, session: AsyncSession = Depends(get_session)):
    q = await session.get(TrafficQuota, quota_id)
    if not q:
        raise HTTPException(404, "Quota not found")
    for k, v in data.items():
        setattr(q, k, v)
    session.add(q)
    await session.commit()
    await session.refresh(q)
    return q


@router.delete("/{quota_id}", status_code=204)
async def delete_quota(quota_id: int, session: AsyncSession = Depends(get_session)):
    q = await session.get(TrafficQuota, quota_id)
    if not q:
        raise HTTPException(404, "Quota not found")
    await session.delete(q)
    await session.commit()


@router.get("/usage", response_model=List[QuotaUsage])
async def current_usage(session: AsyncSession = Depends(get_session)):
    """Current month usage per scope + matching quota info."""
    today = date.today()
    usage_rows = (await session.exec(
        select(TrafficUsage).where(
            TrafficUsage.year == today.year,
            TrafficUsage.month == today.month,
        )
    )).all()

    quotas = (await session.exec(select(TrafficQuota).where(TrafficQuota.enabled == True))).all()  # noqa: E712
    quota_map = {f"{q.scope_type}:{q.scope_id}": q for q in quotas}

    result = []
    for u in usage_rows:
        key = f"{u.scope_type}:{u.scope_id}"
        q = quota_map.get(key)
        total_bytes = (u.bytes_sent or 0) + (u.bytes_recv or 0)
        result.append(QuotaUsage(
            scope_type=u.scope_type,
            scope_id=u.scope_id,
            year=u.year,
            month=u.month,
            bytes_sent=u.bytes_sent,
            bytes_recv=u.bytes_recv,
            total_gb=total_bytes / 1e9,
            limit_gb=q.monthly_limit_gb if q else None,
            action=q.action if q else None,
        ))
    return result
