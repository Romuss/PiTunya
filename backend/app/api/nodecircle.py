"""NodeCircle CRUD + manual rotation trigger."""
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Node, NodeCircle
from app.schemas import NodeCircleCreate, NodeCircleRead, NodeCircleUpdate

router = APIRouter(prefix="/nodecircle", tags=["nodecircle"])

# Hard cap on per-node latency for circle membership (since v1.4.7).
# A NodeCircle rotates between its members on a schedule — high-RTT
# members make rotations visibly slow and produce perceivable stalls
# when the active node fails over to a 200+ ms backup. Rejecting them
# at the API boundary forces the operator to consciously populate the
# circle with nearby servers, rather than quietly shipping a broken
# rotation policy. The cap is hard-coded by operator choice: it
# matches the upper bound of "feels-instant" RTT for home-LAN use.
#
# A node with no `latency_ms` (never health-checked yet, freshly
# imported) is treated as 0 for this check — we don't want to block
# fresh imports just because the health-checker hasn't run yet. The
# first rotation tick will surface the real latency, and an offline
# node is already filtered out by the circle scheduler when picking
# the next candidate.
MAX_LATENCY_MS = 80


async def _validate_node_ids(
    session: AsyncSession, node_ids: List[int]
) -> None:
    """Reject the request if any `node_ids` reference a missing Node
    row OR a Node whose last measured latency exceeds `MAX_LATENCY_MS`.

    Raises `HTTPException(400)` with a list of offenders split into
    "missing" (id absent from DB) and "too_slow" (latency > cap) so
    the UI can show the user exactly which rows to remove.

    Idempotent — fetches all referenced Nodes in one SELECT (no N+1).
    Skipped entirely when `node_ids` is empty (an empty circle is a
    valid intermediate state — e.g. PATCH clearing all members while
    the operator composes a new set; rejecting that would force them
    to delete + recreate instead).
    """
    if not node_ids:
        return
    rows = (await session.exec(
        select(Node.id, Node.name, Node.latency_ms).where(Node.id.in_(node_ids))
    )).all()
    by_id: dict[int, tuple[Optional[str], Optional[int]]] = {}
    for row in rows:
        nid, nm, lat = (row[0], row[1], row[2]) if not hasattr(row, "id") \
            else (row.id, row.name, row.latency_ms)
        by_id[nid] = (nm, lat)
    missing: list[int] = []
    too_slow: list[dict] = []
    for nid in node_ids:
        if nid not in by_id:
            missing.append(nid)
            continue
        nm, lat = by_id[nid]
        # v1.5.0 — latency cap is now a WARNING not a hard reject.
        # The operator may want high-latency nodes in the circle (they
        # can be useful as fallback). The smart rotation + best-candidate
        # logic will naturally prefer lower-latency nodes, and skip
        # high-latency ones during rotation.
        if lat is not None and lat > MAX_LATENCY_MS:
            logger.warning(
                "NodeCircle: node %d (%s) has latency %dms > %dms cap — "
                "allowed but will be deprioritized by best-candidate rotation",
                nid, nm, lat, MAX_LATENCY_MS,
            )
    # Only reject on MISSING nodes (not on slow ones)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"missing node ids: {missing} (referenced but not in DB)",
                "missing": missing,
                "too_slow": [],
            },
        )


@router.get("", response_model=List[NodeCircleRead])
async def list_circles(session: AsyncSession = Depends(get_session)):
    circles = list((await session.exec(select(NodeCircle))).all())

    # Collect every current node_id across all circles, then fetch node names
    # in a single query. Previously this was N+1: one `session.get(Node, ...)`
    # per circle — visible on dashboards with many circles.
    current_ids: set[int] = set()
    circle_payloads = []
    for c in circles:
        data = NodeCircleRead.model_validate(c).model_dump()
        node_ids = data.get("node_ids", [])
        idx = data.get("current_index", 0)
        cur_id: Optional[int] = None
        if node_ids and idx < len(node_ids):
            cur_id = node_ids[idx]
            current_ids.add(cur_id)
        circle_payloads.append((data, cur_id))

    name_by_id: dict[int, str] = {}
    if current_ids:
        rows = (await session.exec(
            select(Node.id, Node.name).where(Node.id.in_(current_ids))
        )).all()
        # .exec() on a multi-column select returns Row objects here
        for row in rows:
            nid, nname = (row[0], row[1]) if not hasattr(row, "id") else (row.id, row.name)
            name_by_id[nid] = nname

    result = []
    for data, cur_id in circle_payloads:
        if cur_id is not None:
            data["current_node_name"] = name_by_id.get(cur_id)
        result.append(NodeCircleRead(**data))
    return result


@router.post("", response_model=NodeCircleRead, status_code=201)
async def create_circle(data: NodeCircleCreate, session: AsyncSession = Depends(get_session)):
    # Latency cap + existence check on members. See `_validate_node_ids`.
    await _validate_node_ids(session, data.node_ids)
    circle = NodeCircle(**data.model_dump(exclude={"node_ids"}))
    circle.node_ids = json.dumps(data.node_ids)
    session.add(circle)
    await session.commit()
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)


@router.get("/{circle_id}", response_model=NodeCircleRead)
async def get_circle(circle_id: int, session: AsyncSession = Depends(get_session)):
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    return NodeCircleRead.model_validate(circle)


@router.patch("/{circle_id}", response_model=NodeCircleRead)
async def update_circle(circle_id: int, data: NodeCircleUpdate, session: AsyncSession = Depends(get_session)):
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    patch = data.model_dump(exclude_unset=True)
    if "node_ids" in patch and patch["node_ids"] is not None:
        # Same latency+existence check as on POST — ensures PATCH can't
        # smuggle in a too-slow member the operator wouldn't be allowed
        # to add via the create endpoint.
        await _validate_node_ids(session, patch["node_ids"])
        patch["node_ids"] = json.dumps(patch["node_ids"])
    for k, v in patch.items():
        setattr(circle, k, v)
    session.add(circle)
    await session.commit()
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)


@router.delete("/{circle_id}", status_code=204)
async def delete_circle(circle_id: int, session: AsyncSession = Depends(get_session)):
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    await session.delete(circle)
    await session.commit()
    from app.core.circle_scheduler import circle_scheduler
    circle_scheduler._next_rotate.pop(circle_id, None)


@router.post("/{circle_id}/rotate", response_model=NodeCircleRead)
async def rotate_now(circle_id: int, session: AsyncSession = Depends(get_session)):
    """Manually trigger rotation to the next node."""
    from app.core.circle_scheduler import circle_scheduler
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    if not circle.enabled:
        raise HTTPException(400, "Cannot rotate a disabled circle")
    await circle_scheduler.rotate_circle(circle_id)
    circle = await session.get(NodeCircle, circle_id)
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)
