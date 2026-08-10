"""AdBlock API — DNS-level ad/tracker blocking management."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select, func
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import AdBlockRule, AdBlockList

router = APIRouter(prefix="/adblock", tags=["adblock"])


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
    last_updated: Optional[str] = None
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
    # Recompile in-memory block set
    from app.core.adblock import compile_rules
    await compile_rules()
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


@router.post("/lists/{list_id}/refresh")
async def refresh_list(list_id: int, session: AsyncSession = Depends(get_session)):
    """Download + parse a blocklist, updating all its rules."""
    from app.core.adblock import download_list
    count = await download_list(list_id)
    return {"downloaded": count}


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def adblock_stats(session: AsyncSession = Depends(get_session)):
    """Summary: total rules, blocklists, top sources."""
    total_rules = (await session.exec(
        select(func.count()).select_from(AdBlockRule)
    )).one()
    enabled_rules = (await session.exec(
        select(func.count()).select_from(AdBlockRule).where(AdBlockRule.enabled == True)  # noqa: E712
    )).one()
    lists = (await session.exec(select(AdBlockList))).all()

    return {
        "total_rules": total_rules,
        "enabled_rules": enabled_rules,
        "blocklists": len(lists),
        "active_lists": sum(1 for l in lists if l.enabled),
        "total_entries": sum(l.entry_count for l in lists),
    }
