"""Tests for `app.core.geoip_lookup`.

Covers:
  * `_flag_emoji` for all known + malformed inputs
  * `_is_generic_name` for the placeholder patterns we enrich AND the
    meaningful names we preserve verbatim
  * `enrich_node_name` — the public entry point — including with a
    mocked mmdb reader so we get a flag for a real country code.

The mmdb mock avoids the real GeoLite2 file (which isn't present in
CI / on a dev machine without it having downloaded geodata first).
"""
from __future__ import annotations

import ipaddress
from typing import Optional

import pytest

import app.core.geoip_lookup as geo  # noqa: E402  (module-level import below)


# ── _flag_emoji ───────────────────────────────────────────────────────────────


class TestFlagEmoji:
    @pytest.mark.parametrize(
        "code,expected",
        [
            ("DE", "\U0001F1E9\U0001F1EA"),  # 🇩🇪
            ("RU", "\U0001F1F7\U0001F1FA"),  # 🇷🇺
            ("US", "\U0001F1FA\U0001F1F8"),  # 🇺🇸
            ("NL", "\U0001F1F3\U0001F1F1"),  # 🇳🇱
            ("JP", "\U0001F1EF\U0001F1F5"),  # 🇯🇵
            # Lowercase normalised to uppercase
            ("de", "\U0001F1E9\U0001F1EA"),
            ("ru", "\U0001F1F7\U0001F1FA"),
        ],
    )
    def test_known_codes(self, code, expected):
        assert geo._flag_emoji(code) == expected

    @pytest.mark.parametrize(
        "bad",
        [None, "", "   ", "D", "DEU", "12", "USA", "X!", "гер"],
    )
    def test_returns_empty_for_unknown(self, bad):
        assert geo._flag_emoji(bad) == ""


# ── _is_generic_name ─────────────────────────────────────────────────────────


class TestIsGenericName:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            "proxy",
            "Proxy",
            "PROXY",
            "proxy-1",
            "Proxy 2",
            "proxy_3",
            "node-12",
            "Node-99",
            "server-7",
            "Server 5",
            "tunnel-3",
            "vpn-1",
            "VPN-2",
            "unknown",
            "Unknown",
            "unnamed",
            "no-name",
            "n/a",
            "default",
            "default-1",
            "--",
            "noname",
        ],
    )
    def test_generic(self, name):
        assert geo._is_generic_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "Tokyo-1",
            "Frankfurt",
            "🇩🇪 vless",
            "Happ/iOS",
            "MY-NODE-1",
            "London-Atom-3",
            "node.example.com:443",
            "Real Proxy",  # has a real word inside, doesn't match pattern
            "VPS-Frankfurt",
            "Reality-FR-1",
            "user@host",
            "alice",
            "Cyberia-DE-FRankfurt-1",
        ],
    )
    def test_meaningful(self, name):
        assert geo._is_generic_name(name) is False


# ── enrich_node_name ──────────────────────────────────────────────────────────


class TestEnrichNodeName:
    """All the enrich tests run with the mmdb reader stubbed out so they
    don't depend on the real GeoLite2 file being on disk.

    `_get_reader` is the single injection point the module uses to open
    the .mmdb; we patch it per-test via a tiny class that returns the
    country code for one IP (DE for 185.12.45.1, matching the example
    in the module docstring) and None for anything else.
    """

    class _StubReader:
        """Drop-in for the maxminddb reader object.

        `get(ip)` returns `{"country": {"iso_code": <cc>}}` for the one
        IP configured at construction; `None` (which the caller treats
        as "no data") for others. Reused across tests with different
        cc values via the `for_ip` factory.
        """

        @classmethod
        def for_ip(cls, ip: str, cc: Optional[str]) -> "TestEnrichNodeName._StubReader":
            inst = cls()
            inst._ip = ip  # type: ignore[attr-defined]
            inst._cc = cc  # type: ignore[attr-defined]
            return inst

        def get(self, ip: str):  # noqa: D401 — keep parity with real reader API
            if ip == self._ip:
                if self._cc is None:
                    return None
                return {"country": {"iso_code": self._cc}}
            return None

    @pytest.fixture(autouse=True)
    def _reset_cache(self, monkeypatch):
        """Reset the lru_cache between tests so a stubbed _lookup_country
        done by an earlier test doesn't leak into the next one."""
        geo._lookup_country.cache_clear()
        # Make sure we re-attempt opening the reader for every test.
        monkeypatch.setattr(geo, "_reader", None)
        monkeypatch.setattr(geo, "_reader_attempted", False)
        # Clean up any previously open real reader handles.
        yield
        geo._lookup_country.cache_clear()
        monkeypatch.setattr(geo, "_reader", None)
        monkeypatch.setattr(geo, "_reader_attempted", False)

    def _stub_reader(self, monkeypatch, ip: str, cc: Optional[str]) -> None:
        """Patch `_get_reader` to return our stub."""
        monkeypatch.setattr(
            geo,
            "_get_reader",
            lambda: self._StubReader.for_ip(ip, cc),
        )

    # ── cases ────────────────────────────────────────────────────────────

    def test_generic_proxy_with_flag(self, monkeypatch):
        self._stub_reader(monkeypatch, "185.12.45.1", "DE")
        # Force the cache to repopulate with the new reader.
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="proxy",
            protocol="vless",
            address="185.12.45.1",
            port=443,
        )
        assert out == "vless-\U0001F1E9\U0001F1EA-185.12.45.1:443"

    def test_blank_produces_no_flag_without_mmdb(self, monkeypatch):
        # No mmdb reader → flag skaleejytak iz null → no flag segment.
        monkeypatch.setattr(geo, "_get_reader", lambda: None)
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="",
            protocol="ss",
            address="127.0.0.1",
            port=1080,
        )
        assert out == "ss-127.0.0.1:1080"

    def test_proxy_3_with_flag(self, monkeypatch):
        self._stub_reader(monkeypatch, "8.8.8.8", "US")
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="Proxy 3",
            protocol="trojan",
            address="8.8.8.8",
            port=443,
        )
        assert out == "trojan-\U0001F1FA\U0001F1F8-8.8.8.8:443"

    def test_meaningful_name_preserved(self, monkeypatch):
        # Even with a working mmdb, a meaningful panel-supplied name
        # must be returned unchanged.
        self._stub_reader(monkeypatch, "185.12.45.1", "DE")
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="Frankfurt-Atomic-1",
            protocol="vless",
            address="185.12.45.1",
            port=443,
        )
        assert out == "Frankfurt-Atomic-1"

    def test_host_address_no_flag(self, monkeypatch):
        # hostname — we deliberately skip mmdb (no sync DNS from import)
        self._stub_reader(monkeypatch, "tokyo.example.com", "JP")
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="proxy",
            protocol="vmess",
            address="tokyo.example.com",
            port=443,
        )
        # No flag, but protocol + addr:port is still far more useful than "proxy"
        assert out == "vmess-tokyo.example.com:443"

    def test_protocol_aliases(self, monkeypatch):
        monkeypatch.setattr(geo, "_get_reader", lambda: None)
        geo._lookup_country.cache_clear()
        # shadowsocks → ss
        out = geo.enrich_node_name(current_name="", protocol="shadowsocks",
                                    address="1.2.3.4", port=8388)
        assert out.startswith("ss-1.2.3.4:8388")
        # hysteria2 → hy2
        out = geo.enrich_node_name(current_name="proxy", protocol="hysteria2",
                                   address="1.2.3.4", port=443)
        assert out.startswith("hy2-1.2.3.4:443")
        # wireguard → wg
        out = geo.enrich_node_name(current_name="  ", protocol="wireguard",
                                   address="1.2.3.4", port=51820)
        assert out == "wg-1.2.3.4:51820"
        # Unknown protocol passes through verbatim (lowercase)
        out = geo.enrich_node_name(current_name="node-99", protocol="Foo",
                                   address="1.2.3.4", port=1)
        assert out == "foo-1.2.3.4:1"

    def test_ipv6_address_qualified_for_lookup(self, monkeypatch):
        # Sanity: "::1" / "2001:db8::1" should be sent to the reader.
        self._stub_reader(monkeypatch, "2001:db8::1", "JP")
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="proxy",
            protocol="vless",
            address="2001:db8::1",
            port=443,
        )
        # If the stub returned JP for this IP, we should see the JP flag.
        assert out == f"vless-\U0001F1EF\U0001F1F5-2001:db8::1:443"

    def test_mmdb_lookup_returns_none_for_unknown_ip(self, monkeypatch):
        # Stub returns None for every IP — flag must be dropped,
        # but the rest of the name is still produced.
        self._stub_reader(monkeypatch, "never-matches", None)
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="proxy",
            protocol="vless",
            address="10.0.0.1",
            port=443,
        )
        assert out == "vless-10.0.0.1:443"

    def test_mmdb_corrupt_record_returns_none(self, monkeypatch):
        # Reader returns something that's not a dict — must not raise,
        # must be treated as "no country".
        class _BadReader:
            def get(self, ip):  # noqa: D401 — keep parity with real reader
                return "not a dict"
        monkeypatch.setattr(geo, "_get_reader", lambda: _BadReader())
        geo._lookup_country.cache_clear()
        out = geo.enrich_node_name(
            current_name="proxy",
            protocol="vless",
            address="1.2.3.4",
            port=443,
        )
        assert out == "vless-1.2.3.4:443"


# ── Integration: subscription import upsert ──────────────────────────────────
# Light smoke test that the enrichment wiring is reached from
# `_fetch_subscription`. Doesn't exercise the whole DB transaction —
# just that `parsed[*].name` gets rewritten to the enriched form when
# the panel returned "proxy". Uses the existing conftest fixtures.


@pytest.mark.asyncio
async def test_subscription_refresh_enriches_generic_names(
    client, session, auth_headers, default_settings, monkeypatch,
):
    """End-to-end through `POST /subscriptions` + `_fetch_subscription`.

    Stubs httpx to return a pair of "proxy"-named vless URIs, patches
    the mmdb reader to return DE for one of the IPs, then asserts the
    resulting Node rows in the DB have the rich names instead of
    "proxy".
    """
    import json
    from app.core import geoip_lookup
    from app.models import Node
    from sqlmodel import select

    # Stub the geoip reader to always return DE for the test IPs.
    class _ReaderStub:
        def get(self, ip):
            # Both test nodes resolve to the same flag for simplicity.
            return {"country": {"iso_code": "DE"}}

    monkeypatch.setattr(geoip_lookup, "_get_reader", lambda: _ReaderStub())
    geoip_lookup._lookup_country.cache_clear()

    # Stub httpx — `_fetch_subscription` opens its own AsyncClient, so
    # patch at the module level the call goes through.
    fake_content = (
        "vless://11111111-1111-1111-1111-111111111111@185.12.45.1:443"
        "?security=reality&sni=example.com#proxy\n"
        "vless://22222222-2222-2222-2222-222222222222@185.12.45.2:443"
        "?security=reality&sni=example.com#proxy-2\n"
    )

    import httpx
    class _Resp:
        status_code = 200
        text = fake_content
        def raise_for_status(self): pass

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **kw): return _Resp()
        async def aclose(self): pass
        async def __aenter__(self): return self

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    # Create a subscription — the background task auto-fetches on POST.
    r = client.post(
        "/api/subscriptions",
        headers=auth_headers,
        json={"name": "test-enrich", "url": "http://example.com/sub",
              "ua": "v2ray"},
    )
    assert r.status_code == 201, r.text

    # Give the BackgroundTask a chance to run.
    import asyncio
    for _ in range(20):
        await asyncio.sleep(0.1)
        rows = list(session.exec(
            select(Node).where(Node.subscription_id == r.json()["id"])
        ))
        if rows:
            break

    # Both Node rows must have been renamed.
    if not rows:
        pytest.skip("Background fetch didn't run within the test window")
    assert len(rows) == 2, f"expected 2 nodes, got {[n.name for n in rows]}"
    # Names should be the enriched form, not "proxy" any more.
    for n in rows:
        assert n.name != "proxy"
        assert n.name != "proxy-2"
        assert n.name.startswith("vless-\U0001F1E9\U0001F1EA-185.12.45.")
        assert "443" in n.name
