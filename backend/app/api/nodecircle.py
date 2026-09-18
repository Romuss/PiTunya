"""NodeCircle CRUD + manual rotation trigger."""
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Node, NodeCircle
from app.schemas import NodeCircleCreate, NodeCircleRead, NodeCircleUpdate

router = APIRouter(prefix="/nodecircle", tags=["nodecircle"])

logger = logging.getLogger(__name__)

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
    session: AsyncSession, node_ids: List[int],
    *,
    auto_remove_missing: bool = False,
) -> List[int]:
    """Validate node IDs for NodeCircle.

    Args:
        session: DB session
        node_ids: List of node IDs to validate
        auto_remove_missing: If True, automatically filter out missing/WireGuard
            nodes and return the cleaned list. If False (default), raise
            HTTPException(400) on invalid nodes (backward-compatible behavior).

    Returns:
        List of valid node IDs. If auto_remove_missing=False and all IDs are
        valid, returns the original list. If auto_remove_missing=True, returns
        the filtered list (may be shorter than input).

    Raises:
        HTTPException(400): When auto_remove_missing=False and invalid nodes
            are found.

    v2.4.0 — also rejects WireGuard nodes. The circle scheduler rotates
    by swapping xray outbounds via the gRPC API, but a WireGuard node is
    an IP tunnel managed by wg-quick — it's not an xray outbound and
    can't be dynamically swapped. Allowing it would produce a silently-
    broken circle that can never rotate to that member.
    """
    if not node_ids:
        return []
    rows = (await session.exec(
        select(Node.id, Node.name, Node.latency_ms, Node.protocol).where(Node.id.in_(node_ids))
    )).all()
    # by_id now carries (name, latency, protocol)
    by_id: dict[int, tuple[Optional[str], Optional[int], Optional[str]]] = {}
    for row in rows:
        if hasattr(row, "id"):
            nid, nm, lat, proto = row.id, row.name, row.latency_ms, row.protocol
        else:
            nid, nm, lat, proto = row[0], row[1], row[2], row[3]
        by_id[nid] = (nm, lat, proto)
    missing: list[int] = []
    wrong_protocol: list[dict] = []
    valid_ids: list[int] = []

    for nid in node_ids:
        if nid not in by_id:
            missing.append(nid)
            continue
        nm, lat, proto = by_id[nid]
        # v2.4.0 — reject WireGuard: it's an IP tunnel, not an xray outbound.
        if proto and proto.lower() == "wireguard":
            wrong_protocol.append({"id": nid, "name": nm, "protocol": proto})
            continue
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
        valid_ids.append(nid)

    # Only reject on MISSING nodes or wrong-protocol (WireGuard) nodes.
    # High latency is just a warning (see above).
    if missing or wrong_protocol:
        if auto_remove_missing:
            # Auto-remove mode: log warnings and return only valid IDs
            if missing:
                logger.warning(
                    "NodeCircle: auto-removing %d missing node IDs: %s",
                    len(missing), missing,
                )
            if wrong_protocol:
                logger.warning(
                    "NodeCircle: auto-removing %d WireGuard node(s): %s",
                    len(wrong_protocol), [n['name'] for n in wrong_protocol],
                )
            return valid_ids
        else:
            # Legacy mode: raise error (backward-compatible)
            parts = []
            if missing:
                parts.append(f"missing node ids: {missing}")
            if wrong_protocol:
                parts.append(
                    f"unsupported protocol (WireGuard can't be rotated): "
                    f"{[n['name'] for n in wrong_protocol]}"
                )
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "; ".join(parts),
                    "missing": missing,
                    "too_slow": [],
                    "wrong_protocol": wrong_protocol,
                },
            )

    return valid_ids


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


@router.get("/{circle_id}/validate")
async def validate_circle(circle_id: int, session: AsyncSession = Depends(get_session)):
    """Validate a NodeCircle and report which node IDs are missing or invalid.

    This endpoint is useful for debugging circles that contain references to
    deleted nodes. It returns the current node_ids along with lists of
    missing IDs and WireGuard nodes (which can't be rotated).
    """
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")

    current_ids = json.loads(circle.node_ids) if isinstance(circle.node_ids, str) else circle.node_ids

    # Fetch existing nodes
    rows = (await session.exec(
        select(Node.id, Node.name, Node.protocol).where(Node.id.in_(current_ids))
    )).all()
    by_id: dict[int, tuple[Optional[str], Optional[str]]] = {}
    for row in rows:
        if hasattr(row, "id"):
            nid, nm, proto = row.id, row.name, row.protocol
        else:
            nid, nm, proto = row[0], row[1], row[2]
        by_id[nid] = (nm, proto)

    missing_ids = [nid for nid in current_ids if nid not in by_id]
    wireguard_nodes = [
        {"id": nid, "name": nm, "protocol": proto}
        for nid, (nm, proto) in by_id.items()
        if proto and proto.lower() == "wireguard"
    ]
    valid_ids = [nid for nid in current_ids if nid in by_id and by_id[nid][1] != "wireguard"]

    return {
        "circle_id": circle_id,
        "circle_name": circle.name,
        "current_node_ids": current_ids,
        "valid_node_ids": valid_ids,
        "missing_node_ids": missing_ids,
        "wireguard_node_ids": wireguard_nodes,
        "is_valid": len(missing_ids) == 0 and len(wireguard_nodes) == 0,
        "auto_fix_available": len(valid_ids) > 0 and (len(missing_ids) > 0 or len(wireguard_nodes) > 0),
    }


@router.post("/{circle_id}/auto-fix", response_model=NodeCircleRead)
async def auto_fix_circle(circle_id: int, session: AsyncSession = Depends(get_session)):
    """Automatically remove missing and WireGuard nodes from a NodeCircle.

    This is useful for repairing circles that have become corrupted due to
    node deletions. Only nodes that pass validation are kept.
    """
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")

    current_ids = json.loads(circle.node_ids) if isinstance(circle.node_ids, str) else circle.node_ids

    # Auto-remove missing/WireGuard nodes
    validated_ids = await _validate_node_ids(
        session, current_ids, auto_remove_missing=True
    )

    if validated_ids == current_ids:
        # No changes needed
        return NodeCircleRead.model_validate(circle)

    circle.node_ids = json.dumps(validated_ids)
    # Reset current_index if it's now out of bounds
    if circle.current_index >= len(validated_ids):
        circle.current_index = 0
    session.add(circle)
    await session.commit()
    await session.refresh(circle)
    return NodeCircleRead.model_validate(circle)


@router.patch("/{circle_id}", response_model=NodeCircleRead)
async def update_circle(circle_id: int, data: NodeCircleUpdate, session: AsyncSession = Depends(get_session)):
    """Update a NodeCircle.

    When `node_ids` is provided, this endpoint automatically filters out
    missing or WireGuard nodes (they can't be rotated). The cleaned list
    is saved and a warning is logged. This avoids the frustrating situation
    where editing a circle fails because nodes were previously deleted
    via other means (DB cleanup, subscription refresh, etc.).

    Creating a new circle (POST) still validates strictly — you can't
    intentionally create a circle with invalid members.
    """
    circle = await session.get(NodeCircle, circle_id)
    if not circle:
        raise HTTPException(404, "NodeCircle not found")
    patch = data.model_dump(exclude_unset=True)
    if "node_ids" in patch and patch["node_ids"] is not None:
        # Auto-remove missing/WireGuard nodes — PATCH is for repairing
        # existing circles, so we gracefully degrade instead of erroring.
        validated_ids = await _validate_node_ids(
            session, patch["node_ids"], auto_remove_missing=True
        )
        patch["node_ids"] = json.dumps(validated_ids)
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
