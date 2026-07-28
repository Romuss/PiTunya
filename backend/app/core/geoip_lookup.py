"""GeoIP lookup + subscription node name enrichment.

Reads the GeoLite2-Country.mmdb file already bind-mounted into the
backend container at `settings.geoip_mmdb_path` (the same file xray
uses for `geoip:XX` routing rules and the same one the GeoData page
in the UI lets the operator update).

Used by `api/subscriptions.py:_fetch_subscription` to give imported
nodes a human-readable name instead of the "proxy" / "proxy-N" /
blank placeholder many panels (Happ, xtool, marzban) ship when they
decline to expose per-server names.

Strategy:
  * Panel-defined names that look meaningful (anything that isn't
    "proxy", "proxy-N", empty, "no-name", "unknown", "node-N",
    "unnamed") are kept verbatim — respect the operator's choice.
  * Otherwise enrich to `<protocol>-<FLAG>-<addr>:<port>`.
  * `maxminddb` is lazy-imported inside `_lookup_country` so the
    backend still boots if the .mmdb is missing (offline installs,
    pre-GeoData-download) — enrichment simply falls back to no flag,
    keeping the protocol/addr/port part still useful.

The lookup is synchronous (mmdb read is ~us-level, in-memory). Called
from inside `_fetch_subscription` which already runs in a background
task, so a few dozen lookups per refresh are negligible.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


# ── Flag emoji from ISO-3166 alpha-2 ──────────────────────────────────────────
# Regional Indicator Symbol code points: U+1F1E6 ('A') … U+1F1FF ('Z').
# Concatenating two of them (one per letter of the country code) renders
# as the flag emoji on essentially every modern OS / browser.
_RIS_OFFSET = 0x1F1E6 - ord('A')


def _flag_emoji(country_code: Optional[str]) -> str:
    """Return the flag emoji for a 2-letter ISO country code.

    Returns empty string for unknown / None / malformed codes so the
    caller can concatenate without checking. (A non-empty input that
    isn't exactly two ASCII letters also returns empty — never raise.)
    """
    if not country_code or not isinstance(country_code, str):
        return ""
    cc = country_code.strip().upper()
    if len(cc) != 2 or not (cc.isalpha() and cc.isascii()):
        return ""
    try:
        return chr(_RIS_OFFSET + ord(cc[0])) + chr(_RIS_OFFSET + ord(cc[1]))
    except (ValueError, OverflowError):
        return ""


# ── mmdb reader (lazy, cached per-process) ───────────────────────────────────

_reader = None  # type: ignore[var-annotated]
_reader_attempted = False


def _get_reader():
    """Open the mmdb reader once, cache for the process lifetime.

    `maxminddb` is imported here (not at module top) so the backend
    starts cleanly when the file is missing — the caller sees `None`
    and degrades. `_reader_attempted` guards against re-trying on every
    lookup once we know it's unavailable (e.g. during a geodata refresh
    where the file is mid-download — every call would otherwise reopen
    it and fail).
    """
    global _reader, _reader_attempted
    if _reader is not None:
        return _reader
    if _reader_attempted:
        return None
    _reader_attempted = True
    try:
        import maxminddb  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "maxminddb not installed — subscription node names will not "
            "be enriched with country flags. Install it via requirements.txt."
        )
        return None
    path = settings.geoip_mmdb_path
    try:
        _reader = maxminddb.open_database(path)
        logger.debug("Opened mmdb for enrichment: %s", path)
    except FileNotFoundError:
        logger.info(
            "GeoLite2 mmdb not found at %s — name enrichment runs without "
            "country flag until geodata is downloaded.", path,
        )
        return None
    except Exception as exc:
        logger.warning("Could not open mmdb %s for enrichment: %s", path, exc)
        return None
    return _reader


@lru_cache(maxsize=4096)
def _lookup_country(ip: str) -> Optional[str]:
    """Return ISO-3166 alpha-2 country code for `ip` or None.

    Cached because subscription refreshes typically re-import the same
    ~hundreds of IP addresses many times — the mmdb read itself is fast
    but the call-overhead + dict-shaped-on-the-C-side lookups add up at
    the 1256-node scale (observed in the wild on a Happ-macos sub).

    On any error (unknown IP, invalid format, mmdb missing) returns
    None — the caller surfaces that as no flag, never an exception.
    """
    reader = _get_reader()
    if reader is None:
        return None
    try:
        rec = reader.get(ip)
    except (ValueError, TypeError):
        # ValueError: invalid IP string. TypeError: wrong arg type.
        return None
    except Exception as exc:
        # maxminddb can raise its own `InvalidDatabaseError` etc. for a
        # corrupted .mmdb; treat as "no data" rather than polluting the
        # caller's flow.
        logger.debug("mmdb lookup failed for %s: %s", ip, exc)
        return None
    if not isinstance(rec, dict):
        return None
    country = rec.get("country")
    if not isinstance(country, dict):
        return None
    iso = country.get("iso_code")
    return iso if isinstance(iso, str) else None


# ── Generic-name detection ────────────────────────────────────────────────────
# Patterns that indicate the panel supplied a "real" name. Anything else
# gets enriched. The list is intentionally permissive: it's much worse
# to overwrite a name the operator deliberately set than to leave a
# placeholder alone (the operator can rename by hand; the panel-hidden
# full list of "proxy-N" they cannot fix without regex magic).
#
# All comparisons are case-insensitive after `strip()`.
#
# Patterns:
#   * literal "" or whitespace
#   * a single word from _GENERIC_LITERALS ("proxy", "node", "server",
#     "tunnel", "vpn", "no-name", "unknown", "unnamed", "n/a")
#   * "<generic>-<n>" or "<generic> <n>" where <generic> is one of the
#     above — matches "proxy-1", "Proxy 2", "node-12", etc.
_GENERIC_LITERALS = frozenset({
    "proxy", "node", "server", "tunnel", "vpn",
    "no-name", "noname", "unknown", "unnamed", "n/a", "na",
    "default", "--",
})

# Pre-compiled regex matching "<generic_literal>[-_\s]<digits>",
# optionally with leading/trailing whitespace.
_generic_numeric_re = re.compile(
    r"^(?:proxy|node|server|tunnel|vpn|"
    r"no-name|noname|unknown|unnamed|"
    r"default)"
    r"[\s\-_]*\d+$",
    re.IGNORECASE,
)


def _is_generic_name(raw: str) -> bool:
    """Return True when `raw` looks like a panel-generated placeholder
    rather than a meaningful user-visible label.

    Treated as generic (will be enriched):
      "" / whitespace
      "proxy", "Proxy", "PROXY"
      "proxy-1", "proxy 2", "node-12", "VPN-3", "Server 4"
      "unknown", "unnamed", "no-name", "n/a"

    Kept verbatim:
      "Tokyo-1", "Frankfurt", "🇩🇪 vless", "Happ/iOS", "MY-NODE-1"
      (i.e. anything that doesn't match the patterns above)
    """
    if raw is None:
        return True
    s = raw.strip()
    if not s:
        return True
    if s.lower() in _GENERIC_LITERALS:
        return True
    if _generic_numeric_re.match(s):
        return True
    return False


# ── Name generation ──────────────────────────────────────────────────────────
# Protocol aliases: collapse the underlying DB protocol onto the
# shorter string users actually expect to see. Keeps `ss` (not
# "shadowsocks"), `hy2` (not "hysteria2"), `naive` (not "naiveproxy").
_PROTOCOL_ALIASES = {
    "shadowsocks": "ss",
    "hysteria2": "hy2",
    "naiveproxy": "naive",
    "wireguard": "wg",
    "socks5": "socks",
    "vless": "vless",
    "vmess": "vmess",
    "trojan": "trojan",
    "socks": "socks",
    "hy2": "hy2",
    "naive": "naive",
    "wg": "wg",
}


def _protocol_label(protocol: str) -> str:
    """Return a short, lowercase, user-friendly name for a protocol."""
    if not protocol:
        return "node"
    return _PROTOCOL_ALIASES.get(protocol.lower(), protocol.lower())


def enrich_node_name(
    *,
    current_name: Optional[str],
    protocol: str,
    address: str,
    port: int,
) -> str:
    """Return the display name for an imported subscription node.

    If `current_name` looks like a meaningful label set by the panel,
    it is returned unchanged — we never overwrite operator-curated
    names or names the panel actually populated. Otherwise a name of
    the form `<protocol>-<🇩🇪>-<addr>:<port>` is generated.

    The flag part is omitted (not "??") when the mmdb file is missing
    or the IP didn't resolve — keeps the name compact and avoids
    surface area for emoji-rendering complaints from older terminals.

    Examples:
      enrich_node_name(current_name="proxy", protocol="vless",
                       address="185.12.45.1", port=443)
      -> "vless-🇩🇪-185.12.45.1:443"

      enrich_node_name(current_name="Tokyo-1", protocol="vless", ...)
      -> "Tokyo-1"  # meaningful — passed through unchanged

      enrich_node_name(current_name="", protocol="ss",
                       address="127.0.0.1", port=1080)
      -> "ss-127.0.0.1:1080"  # loopback has no country → flag omitted
    """
    if not _is_generic_name(current_name):
        return current_name  # type: ignore[return-value]

    proto = _protocol_label(protocol)
    addr = (address or "").strip() or "unknown"
    p = port or 0

    # Resolve country once. If `addr` is a hostname (not an IP literal),
    # skip the lookup — we intentionally do not perform synchronous DNS
    # from inside the import loop (would stall on a flaky resolver and
    # block the background scheduler). Hostname-based addresses will
    # produce "node-vless-host.example.com:443" without a flag, which
    # is still a big UX win over "proxy".
    flag = ""
    # Cheap IPv4 / IPv6 literal check without importing ipaddress:
    # anything that has at least one dot (and looks numeric-ish) OR
    # contains a colon qualifies. Anything else is treated as a
    # hostname and the flag is left blank.
    if _looks_like_ip(addr):
        cc = _lookup_country(addr)
        flag = _flag_emoji(cc)

    parts = [proto]
    if flag:
        parts.append(flag)
    parts.append(f"{addr}:{p}")
    return "-".join(parts)


# Cheap-ish "is this string an IP literal" predicate, used only to
# decide whether to call the mmdb reader. The mmdb library itself
# raises on non-IP input (and we swallow that inside _lookup_country),
# but skipping the call entirely for hostname inputs is faster and
# avoids polluting the lru_cache with negative hits.
#
# We don't reimplement ipaddress.is_ip_address() because the full
# parsing rules are hairy (zone IDs, IPv4-mapped v6, etc.) — anything
# we misclassify here just costs one mmdb lookup, which returns None.
def _looks_like_ip(s: str) -> bool:
    if not s:
        return False
    # IPv4 dotted-quad: 4 dot-separated 1-3 digit runs.
    if s.count(".") == 3 and all(
        p.isdigit() and 0 <= int(p) <= 255 for p in s.split(".")
    ):
        return True
    # Anything with a single ':' smells like IPv6 (covers short forms
    # like "::1", "fd42::1", "::", full "2001:db8::1").
    if ":" in s:
        return True
    return False
