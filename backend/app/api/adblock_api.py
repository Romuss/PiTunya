"""AdBlock API — DNS-level ad/tracker blocking management."""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import AdBlockRule, AdBlockList

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/adblock", tags=["adblock"])


async def _reload_xray_after_adblock_change() -> None:
    """Regenerate xray config + reload so DNS hosts map picks up the
    new blocked domains. Best-effort — if xray isn't running, silently skip."""
    try:
        from app.core.xray import xray_manager
        from app.api.system import _regenerate_and_write
        from app.database import get_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession
        async with AsyncSession(get_async_engine()) as session:
            await _regenerate_and_write(session)
        if xray_manager.is_running:
            await xray_manager.reload()
            logger.info("AdBlock: xray config regenerated + reloaded")
    except Exception as exc:
        logger.warning("AdBlock: xray reload after change failed: %s", exc)


class RuleCreate(BaseModel):
    domain_pattern: str
    rule_type: str = "block"
    source: str = "manual"
    enabled: bool = True


class RuleRead(RuleCreate):
    id: int
    class Config:
        from_attributes = True


class ListCreate(BaseModel):
    name: str
    url: str
    format: str = "hosts"
    enabled: bool = True


class ListRead(ListCreate):
    id: int
    last_updated: Optional[datetime] = None
    entry_count: int = 0
    class Config:
        from_attributes = True
        


# ── Rules ────────────────────────────────────────────────────────────────────

@router.get("/rules", response_model=List[RuleRead])
async def list_rules(
    source: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    query = select(AdBlockRule)
    if source:
        query = query.where(AdBlockRule.source == source)
    return (await session.exec(query)).all()


@router.post("/rules", response_model=RuleRead, status_code=201)
async def create_rule(data: RuleCreate, session: AsyncSession = Depends(get_session)):
    r = AdBlockRule(**data.model_dump())
    session.add(r)
    await session.commit()
    await session.refresh(r)
    # Recompile in-memory block set + reload xray DNS
    from app.core.adblock import compile_rules
    await compile_rules()
    await _reload_xray_after_adblock_change()
    return r


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    r = await session.get(AdBlockRule, rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    await session.delete(r)
    await session.commit()
    from app.core.adblock import compile_rules
    await compile_rules()
    await _reload_xray_after_adblock_change()


# ── Lists ────────────────────────────────────────────────────────────────────

@router.get("/lists", response_model=List[ListRead])
async def list_lists(session: AsyncSession = Depends(get_session)):
    return (await session.exec(select(AdBlockList))).all()


@router.post("/lists", response_model=ListRead, status_code=201)
async def create_list(data: ListCreate, session: AsyncSession = Depends(get_session)):
    lst = AdBlockList(**data.model_dump())
    session.add(lst)
    await session.commit()
    await session.refresh(lst)
    return lst


@router.patch("/lists/{list_id}", response_model=ListRead)
async def update_list(list_id: int, data: dict, session: AsyncSession = Depends(get_session)):
    """Update a blocklist (enable/disable, rename, etc.)."""
    lst = await session.get(AdBlockList, list_id)
    if not lst:
        raise HTTPException(404, "List not found")
    for k, v in data.items():
        if hasattr(lst, k):
            setattr(lst, k, v)
    session.add(lst)
    await session.commit()
    await session.refresh(lst)
    return lst


@router.delete("/lists/{list_id}", status_code=204)
async def delete_list(list_id: int, session: AsyncSession = Depends(get_session)):
    lst = await session.get(AdBlockList, list_id)
    if not lst:
        raise HTTPException(404, "List not found")
    # Delete all rules from this list
    from sqlmodel import delete
    await session.exec(delete(AdBlockRule).where(AdBlockRule.source == lst.name))
    await session.delete(lst)
    await session.commit()
    from app.core.adblock import compile_rules
    await compile_rules()
    await _reload_xray_after_adblock_change()


@router.post("/lists/{list_id}/refresh")
async def refresh_list(list_id: int):
    """Download + parse a blocklist, updating all its rules.

    No session dependency — download_list creates its own session
    (avoids MissingGreenlet from nested async session).
    """
    from app.core.adblock import download_list
    count = await download_list(list_id)
    await _reload_xray_after_adblock_change()
    return {"downloaded": count}


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def adblock_stats(session: AsyncSession = Depends(get_session)):
    """Summary: total rules, blocklists, top sources + blocking stats."""
    total_rules = (await session.exec(
        select(func.count()).select_from(AdBlockRule)
    )).one()
    enabled_rules = (await session.exec(
        select(func.count()).select_from(AdBlockRule).where(AdBlockRule.enabled == True)  # noqa: E712
    )).one()
    lists = (await session.exec(select(AdBlockList))).all()

    # v2.0.2 — blocking stats from in-memory counters
    from app.core.adblock import get_block_stats
    block_stats = get_block_stats()

    return {
        "total_rules": total_rules,
        "enabled_rules": enabled_rules,
        "blocklists": len(lists),
        "active_lists": sum(1 for l in lists if l.enabled),
        "total_entries": sum(l.entry_count for l in lists),
        # Blocking stats (v2.0.2)
        "total_blocked": block_stats["total_blocked"],
        "unique_domains_blocked": block_stats["unique_domains_blocked"],
        "top_blocked": block_stats["top_blocked"],
    }


@router.post("/stats/reset")
async def reset_blocking_stats():
    """Reset blocking statistics counters."""
    from app.core.adblock import reset_block_stats
    reset_block_stats()
    return {"status": "ok"}
