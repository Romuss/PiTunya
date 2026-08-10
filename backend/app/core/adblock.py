"""Ad blocking engine — DNS-level filtering using xray's hosts map.

Two-layer approach:
1. Manual rules (AdBlockRule table) — explicit block/allow domains
2. Subscribed blocklists (AdBlockList table) — auto-downloaded lists
   (StevenBlack hosts, EasyList domain version, etc.)

Integration: config_gen adds a xray DNS server entry with a `hosts` map
that returns 0.0.0.0 for blocked domains. No separate AdGuard container.

Check flow: domain → check_adblock(domain) → block (return 0.0.0.0) | allow
"""
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Set
from collections import defaultdict

from sqlmodel import select

from app.database import get_async_engine
from app.models import AdBlockRule, AdBlockList

from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# In-memory block set — compiled from DB on startup + on rule change
_blocked_domains: Set[str] = set()
_allowed_domains: Set[str] = set()
_last_compile: Optional[datetime] = None
_compile_lock = asyncio.Lock()

# Master switch — when False, AdBlock is completely disabled (no DNS
# hosts map, no check_domain matches). Set via set_enabled(False) by
# the API or a kill-switch toggle. Prevents xray from loading 254k
# domains into DNS hosts when resources are tight.
_adblock_enabled: bool = True

# Blocking stats (v2.0.2) — in-memory counters, reset on restart
_block_stats: dict[str, int] = {}  # domain → block count
_total_blocked: int = 0
_stats_lock = asyncio.Lock()

# Default blocklists (seeded on first run)
DEFAULT_LISTS = [
    {
        "name": "StevenBlack Unified Hosts",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        "format": "hosts",
        "enabled": True,
    },
    {
        "name": "AdGuard DNS Filter",
        "url": "https://filters.adtidy.org/extension/chromium/filters/15.txt",
        "format": "domain",
        "enabled": False,  # opt-in — large list
    },
]


async def compile_rules() -> None:
    """Rebuild the in-memory block/allow sets from DB.

    Called on startup + after any rule/list change.
    """
    global _blocked_domains, _allowed_domains, _last_compile

    async with _compile_lock:
        blocked = set()
        allowed = set()

        async with AsyncSession(get_async_engine()) as session:
            # Manual rules
            rules = (await session.exec(
                select(AdBlockRule).where(AdBlockRule.enabled == True)  # noqa: E712
            )).all()

            for r in rules:
                domain = r.domain_pattern.lower().strip()
                if r.rule_type == "block":
                    blocked.add(domain)
                elif r.rule_type == "allow":
                    allowed.add(domain)

            # Blocklist entries (from downloaded lists — stored as AdBlockRule
            # rows with source = list name)
            # The actual domains from blocklists are stored as AdBlockRule

        _blocked_domains = blocked
        _allowed_domains = allowed
        _last_compile = datetime.now(tz=timezone.utc)
        logger.info(
            "AdBlock: compiled %d blocked + %d allowed domains",
            len(blocked), len(allowed),
        )


def is_enabled() -> bool:
    """Check if AdBlock master switch is on."""
    return _adblock_enabled


def set_enabled(enabled: bool) -> None:
    """Master kill-switch — when False, AdBlock is completely disabled.
    No DNS hosts map, no check_domain matches.
    """
    global _adblock_enabled
    _adblock_enabled = enabled
    logger.info("AdBlock: %s", "enabled" if enabled else "DISABLED (kill-switch)")


def check_domain(domain: str) -> bool:
    """Check if a domain should be blocked.

    Returns True if blocked, False if allowed.
    Domain matching: exact match + wildcard (*.example.com matches sub.example.com).
    Records block stats (v2.0.2) — increments per-domain counter.
    """
    if not domain or not _adblock_enabled:
        return False

    domain = domain.lower().rstrip(".")

    # Check allow list first (whitelist overrides block)
    if domain in _allowed_domains:
        return False

    blocked = False

    # Check exact block
    if domain in _blocked_domains:
        blocked = True

    # Check wildcard patterns (*.example.com)
    if not blocked:
        parts = domain.split(".")
        for i in range(1, len(parts)):
            wildcard = "*." + ".".join(parts[i:])
            if wildcard in _blocked_domains:
                blocked = True
                break

    # Record stats (v2.0.2)
    if blocked:
        global _total_blocked
        _total_blocked += 1
        _block_stats[domain] = _block_stats.get(domain, 0) + 1

    return blocked


def get_block_stats() -> dict:
    """Return blocking statistics (v2.0.2).

    Returns: {"total_blocked": int, "top_blocked": [(domain, count), ...]}
    """
    top = sorted(_block_stats.items(), key=lambda x: x[1], reverse=True)[:20]
    return {
        "total_blocked": _total_blocked,
        "unique_domains_blocked": len(_block_stats),
        "top_blocked": [{"domain": d, "count": c} for d, c in top],
    }


def reset_block_stats() -> None:
    """Reset blocking stats (v2.0.2)."""
    global _total_blocked
    _total_blocked = 0
    _block_stats.clear()


def get_blocked_domains_for_config() -> dict[str, str]:
    """Return a {domain: "0.0.0.0"} map for xray DNS hosts config.

    Returns empty dict when AdBlock is disabled (kill-switch).
    Called by config_gen when building the DNS section.
    """
    if not _adblock_enabled:
        return {}
    return {d: "0.0.0.0" for d in _blocked_domains if not d.startswith("*")}


async def download_list(list_id: int) -> int:
    """Download + parse a blocklist, store as AdBlockRule rows.

    Returns the number of domains added.
    """
    import httpx

    # 1. Fetch list metadata (capture attributes before session closes)
    async with AsyncSession(get_async_engine()) as session:
        lst = await session.get(AdBlockList, list_id)
        if not lst:
            return 0
        list_name = lst.name
        list_url = lst.url
        list_format = lst.format
        list_enabled = lst.enabled

    # 2. Download the list (no DB session)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(list_url)
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:
        logger.error("AdBlock: failed to download %s: %s", list_name, exc)
        return 0

    # 3. Parse
    domains: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if list_format == "hosts":
            parts = line.split()
            if len(parts) >= 2:
                domain = parts[-1].lower()
                if domain and domain not in ("localhost",):
                    domains.append(domain)
        elif list_format == "domain":
            domain = line.lstrip("|").rstrip("^").lower()
            if domain and "." in domain:
                domains.append(domain)

    domains = list(set(domains))
    added = len(domains)

    # 4. Store in DB (separate session, batched commits)
    from sqlmodel import delete as sqlmodel_delete
    async with AsyncSession(get_async_engine()) as session:
        await session.exec(
            sqlmodel_delete(AdBlockRule).where(AdBlockRule.source == list_name)
        )
        await session.commit()

        now = datetime.now(tz=timezone.utc)
        batch_size = 500
        for i in range(0, len(domains), batch_size):
            batch = domains[i:i + batch_size]
            for domain in batch:
                session.add(AdBlockRule(
                    domain_pattern=domain,
                    rule_type="block",
                    source=list_name,
                    enabled=list_enabled,
                ))
            await session.commit()

        # Update list metadata (fresh fetch to avoid expired object)
        lst_obj = await session.get(AdBlockList, list_id)
        if lst_obj:
            lst_obj.last_updated = now
            lst_obj.entry_count = added
            session.add(lst_obj)
            await session.commit()

    logger.info("AdBlock: downloaded %s, %d domains", list_name, added)

    # 5. Recompile (separate session)
    await compile_rules()
    return added


async def seed_default_lists() -> None:
    """Seed default blocklists on first run (if table is empty)."""
    async with AsyncSession(get_async_engine()) as session:
        existing = (await session.exec(select(AdBlockList))).all()
        if existing:
            return

        for lst_data in DEFAULT_LISTS:
            session.add(AdBlockList(**lst_data))
        await session.commit()
        logger.info("AdBlock: seeded %d default blocklists", len(DEFAULT_LISTS))
