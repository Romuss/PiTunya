"""Configuration Export/Import endpoints.

GET  /api/system/export    → downloads JSON bundle of all PiTun config
POST /api/system/import    → restores from uploaded JSON bundle

Exported data includes:
  - Settings (key-value pairs from the Settings table)
  - Nodes (all fields, redacted secrets optional)
  - RoutingRules
  - DNSRules
  - NodeCircles
  - Subscriptions (without password/secret fields)
  - BalancerGroups

Import validates the shape and upserts each table. Existing rows with
matching natural keys are updated; new rows are inserted. Secrets
(passwords, UUIDs, private keys) are included by default but can be
redacted with `?redact=true` on export.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import (
    Node, RoutingRule, DNSRule, NodeCircle, Subscription,
    BalancerGroup, Settings as DBSettings, Event, User,
)
from app.config import APP_VERSION

logger = logging.getLogger(__name__)

router = APIRouter()

# Models to export/import — each entry: (model_class, list of field names to include)
_EXPORT_MODELS = [
    ("settings", DBSettings, ["key", "value"]),
    ("nodes", Node, None),  # None = all fields
    ("routing_rules", RoutingRule, None),
    ("dns_rules", DNSRule, None),
    ("node_circles", NodeCircle, None),
    ("subscriptions", Subscription, None),
    ("balancer_groups", BalancerGroup, None),
]

# Models to skip on import (read-only, internal, or sensitive)
_SKIP_IMPORT_KEYS = {"settings"}  # settings are imported but keys are filtered

# Settings keys that should NOT be exported (secrets, internal state)
_SKIP_SETTING_KEYS = {
    "active_node_id",  # instance-specific
}


@router.get("/system/export")
async def export_config(
    redact: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Export full PiTun configuration as a JSON file.

    Set ?redact=true to strip out passwords, UUIDs, private keys
    and other secrets — useful for sharing a configuration template.
    """
    bundle: Dict[str, Any] = {
        "version": APP_VERSION,
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "redacted": redact,
    }

    for key, model_cls, fields in _EXPORT_MODELS:
        rows = (await session.exec(select(model_cls))).all()
        if key == "settings":
            # Filter out internal keys
            items = []
            for row in rows:
                if row.key in _SKIP_SETTING_KEYS:
                    continue
                items.append({"key": row.key, "value": row.value})
            bundle[key] = items
        else:
            items = []
            for row in rows:
                d = row.model_dump()
                if redact:
                    # Strip secret fields
                    for secret_field in ["uuid", "password", "wg_private_key",
                                         "wg_preshared_key", "private_key",
                                         "reality_pbk", "reality_sid", "reality_spx"]:
                        if secret_field in d and d[secret_field]:
                            d[secret_field] = "***REDACTED***"
                # Remove auto-managed fields
                for auto_field in ["id", "latency_ms", "last_check", "is_online",
                                   "speed_mbps", "last_speed_test", "last_updated",
                                   "last_error", "node_count", "last_rotated",
                                   "current_index", "client_orphan",
                                   "from_deployment_client_id"]:
                    d.pop(auto_field, None)
                items.append(d)
            bundle[key] = items

    return bundle


@router.post("/system/import")
async def import_config(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Import configuration from an uploaded JSON file.

    Upserts each table: existing rows matched by natural key are
    updated, new rows are inserted. This is NOT a destructive import —
    existing data that isn't in the bundle is left untouched.
    """
    content = await file.read()
    try:
        bundle = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Invalid JSON: {exc}")

    if "version" not in bundle:
        raise HTTPException(400, "Missing 'version' field in bundle")

    results: Dict[str, Dict[str, int]] = {}

    # Import settings
    if "settings" in bundle and isinstance(bundle["settings"], list):
        added = updated = 0
        for item in bundle["settings"]:
            key = item.get("key")
            value = item.get("value", "")
            if not key or key in _SKIP_SETTING_KEYS:
                continue
            existing = (await session.exec(
                select(DBSettings).where(DBSettings.key == key)
            )).first()
            if existing:
                existing.value = value
                session.add(existing)
                updated += 1
            else:
                session.add(DBSettings(key=key, value=value))
                added += 1
        results["settings"] = {"added": added, "updated": updated}

    # Import nodes
    if "nodes" in bundle and isinstance(bundle["nodes"], list):
        added = updated = 0
        for item in bundle["nodes"]:
            # Match by (protocol, address, port, uuid) — the natural key
            protocol = item.get("protocol", "")
            address = item.get("address", "")
            port = item.get("port", 0)
            uuid_val = item.get("uuid", "")
            existing = None
            if address and port:
                existing = (await session.exec(
                    select(Node).where(
                        Node.protocol == protocol,
                        Node.address == address,
                        Node.port == port,
                    )
                )).first()
            if existing:
                # Update fields
                for k, v in item.items():
                    if hasattr(existing, k) and k != "id":
                        setattr(existing, k, v)
                session.add(existing)
                updated += 1
            else:
                # Insert new
                clean = {k: v for k, v in item.items() if hasattr(Node, k)}
                session.add(Node(**clean))
                added += 1
        results["nodes"] = {"added": added, "updated": updated}

    # Import routing rules
    if "routing_rules" in bundle and isinstance(bundle["routing_rules"], list):
        added = updated = 0
        for item in bundle["routing_rules"]:
            name = item.get("name", "")
            rule_type = item.get("rule_type", "")
            match_value = item.get("match_value", "")
            existing = (await session.exec(
                select(RoutingRule).where(
                    RoutingRule.name == name,
                    RoutingRule.rule_type == rule_type,
                    RoutingRule.match_value == match_value,
                )
            )).first()
            if existing:
                for k, v in item.items():
                    if hasattr(existing, k) and k != "id":
                        setattr(existing, k, v)
                session.add(existing)
                updated += 1
            else:
                clean = {k: v for k, v in item.items() if hasattr(RoutingRule, k)}
                session.add(RoutingRule(**clean))
                added += 1
        results["routing_rules"] = {"added": added, "updated": updated}

    # Import DNS rules
    if "dns_rules" in bundle and isinstance(bundle["dns_rules"], list):
        added = updated = 0
        for item in bundle["dns_rules"]:
            name = item.get("name", "")
            existing = (await session.exec(
                select(DNSRule).where(DNSRule.name == name)
            )).first() if name else None
            if existing:
                for k, v in item.items():
                    if hasattr(existing, k) and k != "id":
                        setattr(existing, k, v)
                session.add(existing)
                updated += 1
            else:
                clean = {k: v for k, v in item.items() if hasattr(DNSRule, k)}
                session.add(DNSRule(**clean))
                added += 1
        results["dns_rules"] = {"added": added, "updated": updated}

    # Import node circles
    if "node_circles" in bundle and isinstance(bundle["node_circles"], list):
        added = updated = 0
        for item in bundle["node_circles"]:
            name = item.get("name", "")
            existing = (await session.exec(
                select(NodeCircle).where(NodeCircle.name == name)
            )).first() if name else None
            if existing:
                for k, v in item.items():
                    if hasattr(existing, k) and k != "id":
                        setattr(existing, k, v)
                session.add(existing)
                updated += 1
            else:
                clean = {k: v for k, v in item.items() if hasattr(NodeCircle, k)}
                session.add(NodeCircle(**clean))
                added += 1
        results["node_circles"] = {"added": added, "updated": updated}

    # Import subscriptions
    if "subscriptions" in bundle and isinstance(bundle["subscriptions"], list):
        added = updated = 0
        for item in bundle["subscriptions"]:
            name = item.get("name", "")
            existing = (await session.exec(
                select(Subscription).where(Subscription.name == name)
            )).first() if name else None
            if existing:
                for k, v in item.items():
                    if hasattr(existing, k) and k != "id":
                        setattr(existing, k, v)
                session.add(existing)
                updated += 1
            else:
                clean = {k: v for k, v in item.items() if hasattr(Subscription, k)}
                session.add(Subscription(**clean))
                added += 1
        results["subscriptions"] = {"added": added, "updated": updated}

    # Import balancer groups
    if "balancer_groups" in bundle and isinstance(bundle["balancer_groups"], list):
        added = updated = 0
        for item in bundle["balancer_groups"]:
            name = item.get("name", "")
            existing = (await session.exec(
                select(BalancerGroup).where(BalancerGroup.name == name)
            )).first() if name else None
            if existing:
                for k, v in item.items():
                    if hasattr(existing, k) and k != "id":
                        setattr(existing, k, v)
                session.add(existing)
                updated += 1
            else:
                clean = {k: v for k, v in item.items() if hasattr(BalancerGroup, k)}
                session.add(BalancerGroup(**clean))
                added += 1
        results["balancer_groups"] = {"added": added, "updated": updated}

    await session.commit()

    logger.info("Config import complete: %s", results)
    return {"status": "ok", "results": results}
