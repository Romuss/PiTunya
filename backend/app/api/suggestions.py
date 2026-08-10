"""Rule suggestions API — accept/dismiss DNS-log-derived routing suggestions."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import SuggestedRule, RoutingRule, Node

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


class SuggestionRead(BaseModel):
    id: int
    domain: str
    query_count: int
    suggestion_type: str
    reason: str
    status: str
    suggested_node_id: Optional[int] = None
    class Config:
        from_attributes = True


@router.get("", response_model=List[SuggestionRead])
async def list_suggestions(
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    query = select(SuggestedRule)
    if status:
        query = query.where(SuggestedRule.status == status)
    else:
        query = query.where(SuggestedRule.status == "pending")
    query = query.order_by(SuggestedRule.query_count.desc())
    return (await session.exec(query)).all()


@router.post("/{suggestion_id}/accept")
async def accept_suggestion(suggestion_id: int, session: AsyncSession = Depends(get_session)):
    """Create a RoutingRule from the suggestion and mark it accepted."""
    s = await session.get(SuggestedRule, suggestion_id)
    if not s:
        raise HTTPException(404, "Suggestion not found")
    if s.status != "pending":
        raise HTTPException(400, f"Suggestion already {s.status}")

    # Create a routing rule: domain → suggested node
    if s.suggested_node_id:
        node = await session.get(Node, s.suggested_node_id)
        if not node:
            raise HTTPException(400, "Suggested node no longer exists")

        # Check if a rule for this domain already exists
        existing = (await session.exec(
            select(RoutingRule).where(
                RoutingRule.match_type == "domain",
                RoutingRule.match_value == s.domain,
            )
        )).first()

        if existing:
            # Update existing rule's action
            existing.action = f"node:{s.suggested_node_id}"
            existing.enabled = True
            session.add(existing)
        else:
            # Get max order
            max_order = (await session.exec(
                select(RoutingRule).order_by(RoutingRule.order.desc()).limit(1)
            )).first()
            next_order = (max_order.order + 10) if max_order else 10

            session.add(RoutingRule(
                name=f"Auto: {s.domain}",
                rule_type="domain",
                match_value=s.domain,
                action=f"node:{s.suggested_node_id}",
                enabled=True,
                order=next_order,
            ))

    s.status = "accepted"
    session.add(s)
    await session.commit()
    return {"status": "accepted", "domain": s.domain}


@router.post("/{suggestion_id}/dismiss")
async def dismiss_suggestion(suggestion_id: int, session: AsyncSession = Depends(get_session)):
    s = await session.get(SuggestedRule, suggestion_id)
    if not s:
        raise HTTPException(404, "Suggestion not found")
    s.status = "dismissed"
    session.add(s)
    await session.commit()
    return {"status": "dismissed"}
