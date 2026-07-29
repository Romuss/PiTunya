# PiTunya

**🌐 English** · [Русский](README.ru.md)

> **Fork of [PiTun](https://github.com/DaveBugg/PiTun)** by Romuss.
> Credit for the original project goes to DaveBugg.

Self-hosted transparent proxy manager for Raspberry Pi 4/5 (and any Linux box).
Intercepts LAN traffic via nftables TPROXY, routes through xray-core based on
your rules — domain, GeoIP, GeoSite, MAC, port, protocol — with a web UI.

[![CI](https://img.shields.io/github/actions/workflow/status/Romuss/PiTunya/ci.yml?branch=master&label=CI)](#)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%2Famd64%20%7C%20linux%2Farm64-lightgrey)](#)

---

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/Romuss/PiTunya/master/install.sh | sudo bash
```

That's it. After 5 minutes your PiTun box is up at `http://<box-ip>`.

**From your fork:**
```bash
curl -fsSL https://raw.githubusercontent.com/Romuss/PiTunya/master/install.sh \
  | sudo bash -s -- --repo Romuss/PiTunya --branch master
```

**For a specific build (no pre-built release):**
```bash
curl -fsSL https://cdn.jsdelivr.net/gh/Romuss/PiTunya@master/install.sh \
  | sudo bash -s -- --repo Romuss/PiTunya --branch master --build
```

---

## What's different from the original PiTun?

### Security
- **SECRET_KEY boot guard** — refuses to start with default `changeme` key
- **Per-subscription `allow_insecure`** — TLS verification per subscription
- **Login rate-limiting + DB lockout** — 5 wrong attempts → 1h lockout
- **Optional TLS-on-LAN** — Caddy reverse proxy via `COMPOSE_PROFILES=tls`
- **Service supervisor** — startup failures visible in Recent Events

### Anti-censorship
- **`dns_route_via`** — route DNS through proxy to hide from ISP DPI
- **`disable_ipv6`** default true — closes IPv6 bypass leak
- **`proxy_local_apps`** — box's own traffic bypasses VPN by default

### NodeCircle (smart rotation)
- **"Best" mode** — auto-pick best available node (online + lowest latency + highest speed)
- **Smart rotation** — skip rotation when active node is healthy
- **`min_speed_mbps`** + **`max_latency_ms`** — filter candidates by quality
- **Auto-sync from subscription** — node_ids update automatically on refresh

### Nodes page
- **Per-node speed test** — throughput measurement with progress indicator
- **Quality sort** — sort by availability + speed + latency
- **Speed badge** — `⚡ XX MB/s` on each node card
- **Name enrichment** — `proxy` → `vless-🇬🇧-185.12.45.1:443` using GeoLite2

### Operational
- **Config Export/Import** — backup and restore full configuration
- **`scripts/backup.sh`** — online SQLite backup + rotation + encryption
- **`scripts/update.sh`** — one-command update with auto-rollback
- **Background speed test scheduler** — auto-tests active node hourly

---

## ⚠️ Important — Routing Loop Prevention

When devices use the PiTun host as their default gateway, the host needs a
**second IP** for its own outbound traffic:

```bash
# Primary IP — devices set this as their gateway (GATEWAY_IP)
ip addr add 192.168.1.100/24 dev eth0

# Secondary IP — host uses this for its own traffic (bypasses TPROXY)
ip addr add 192.168.1.101/24 dev eth0
ip route replace default via 192.168.1.1 src 192.168.1.101
```

Without the second IP, the host's own packets get TPROXY'd back into xray
→ `proxy_local_apps` setting is the fallback, but second IP is more reliable.

---

## Key Configuration

| Variable | Default | What |
|---|---|---|
| `SECRET_KEY` | `changeme` | JWT signing key — `openssl rand -hex 32` (required!) |
| `INTERFACE` | `eth0` | LAN interface name |
| `LAN_CIDR` | `192.168.1.0/24` | Your LAN subnet (autodetected) |
| `GATEWAY_IP` | `192.168.1.100` | PiTun host's LAN IP — devices use as gateway |
| `TPROXY_PORT_TCP` | `7893` | TPROXY TCP listener |
| `SOCKS_PORT` | `1080` | SOCKS5 explicit proxy |
| `HTTP_PORT` | `8080` | HTTP explicit proxy |
| `DNS_PORT` | `5353` | Internal DNS forwarder |

Full annotated example: [`.env.example`](.env.example).

All runtime config through the web UI. Only `.env` needs manual setup.

---

## Supported Protocols

VLESS, VMess, Trojan, Shadowsocks, WireGuard, Hysteria2, NaiveProxy, SOCKS5

---

## Three Proxy Endpoints

| Endpoint | Port | Use case |
|---|---|---|
| TPROXY | `7893` | Transparent gateway — devices set as gateway |
| SOCKS5 | `1080` | Explicit proxy for browsers/apps |
| HTTP | `8080` | For apps without SOCKS5 support |

---

## Tech Stack

- **Backend:** Python 3.11, FastAPI, SQLModel (SQLite), xray-core 26.x
- **Frontend:** React 19, Vite, TailwindCSS, TanStack Query, Zustand
- **Container:** Docker Compose, multi-arch (arm64 + amd64)
- **Network:** nftables TPROXY, DNS sniffing, GeoIP/GeoSite (Loyalsoldier)

---

## Keywords

`vpn` `proxy` `xray` `v2ray` `vless` `vmess` `trojan` `shadowsocks` `wireguard` `naiveproxy` `tproxy` `nftables` `raspberry-pi` `rpi` `bypass` `censorship` `anti-dpi` `mtproto` `telegram` `subscription` `node-circle` `rotation` `speed-test` `geoip` `transparent-proxy` `lan` `gateway` `self-hosted` `docker`

---

## License

BSD-3-Clause — same as upstream PiTun.
