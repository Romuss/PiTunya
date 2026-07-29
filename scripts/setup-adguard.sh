#!/bin/bash
# AdGuard Home initial configuration for PiTun sidecar
# Created at install time — AdGuard Home needs this before first boot.

bind_port: 80
allow_start: true
language: en
dns:
  bind_hosts:
  - 0.0.0.0
  port: 53
  protection_enabled: true
  filtering_enabled: true
  blocking_mode: nxdomain
  blocked_response_ttl: 10
  upstream_dns:
  - https://dns.google/dns-query
  - https://cloudflare-dns.com/dns-query
  bootstrap_dns:
  - 8.8.8.8
  - 1.1.1.1
  all_servers: true
  upstream_mode: load_balance
  cache_size: 4194304
  cache_ttl_min: 0
  cache_ttl_max: 86400
  cache_optimistic: true
  aaaa_disabled: false
filters:
- enabled: true
  url: https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt
  name: AdGuard DNS filter
  id: 1
- enabled: true
  url: https://raw.githubusercontent.com/AdguardTeam/AdGuardFilters/master/SpywareFilter/sections/tracking_servers_firstparty.txt
  name: AdGuard Tracking Servers
  id: 2
- enabled: true
  url: https://raw.githubusercontent.com/AdguardTeam/AdGuardFilters/master/BaseFilter/sections/adservers_firstparty.txt
  name: AdGuard Adservers
  id: 3
- enabled: true
  url: https://easylist-downloads.adblockplus.org/easylist.txt
  name: EasyList
  id: 4
- enabled: true
  url: https://easylist-downloads.adblockplus.org/easyprivacy.txt
  name: EasyPrivacy
  id: 5
- enabled: true
  url: https://raw.githubusercontent.ru/easylist-ru/easylist-ru/master/easylist-ru.txt
  name: RU AdList
  id: 6
- enabled: true
  url: https://adaway.org/hosts.txt
  name: AdAway
  id: 7
- enabled: true
  url: https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
  name: StevenBlack Hosts
  id: 8
whitelist_filters: []
user_rules: []
clients:
  runtime_sources: []
  persistent: []
http:
  address: 0.0.0.0
  session_ttl: 720h
auth_attempts: 5
block_ttl_min: 15
querylog:
  enabled: true
  file_enabled: true
  interval: 2160h
  memory_size: 1000
  ignored: []
stats:
  enabled: false
  interval: 1h
  ignored: []
schema_version: 20
