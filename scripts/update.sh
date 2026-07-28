#!/bin/bash
# ============================================================================
# PiTun — update script for v1.4.7 → v1.4.8 (or any incremental update)
# ============================================================================
# Run on the PiTun-box: sudo bash /opt/pitun/scripts/update.sh
#
# What it does:
#   1. Backup DB + .env
#   2. Pull fresh code from fork (git clone to /tmp, overlay onto /opt/pitun)
#   3. Restart backend (alembic upgrade head runs automatically)
#   4. Verify health
#   5. If broken — rollback + restore
# ============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }

REPO="${PITUN_REPO:-Romuss/PiTunya}"
BRANCH="${PITUN_BRANCH:-master}"
INSTALL_DIR="${PITUN_DIR:-/opt/pitun}"

[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0" && exit 1

echo "============================================"
echo "  PiTun Update Script"
echo "  Repo:   $REPO"
echo "  Branch: $BRANCH"
echo "  Target: $INSTALL_DIR"
echo "============================================"
echo

# ── 1. Backup ────────────────────────────────────────────────────────────
info "Step 1: Backup"
TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)

if [[ -f "$INSTALL_DIR/data/pitun.db" ]]; then
    cp -a "$INSTALL_DIR/data/pitun.db" "/root/pitun-backup-$TS.db"
    ok "DB backed up: /root/pitun-backup-$TS.db"
else
    warn "No DB found at $INSTALL_DIR/data/pitun.db — skipping DB backup"
fi

if [[ -f "$INSTALL_DIR/.env" ]]; then
    cp -a "$INSTALL_DIR/.env" "/root/pitun-backup-$TS.env"
    ok ".env backed up: /root/pitun-backup-$TS.env"
fi

# Tag current image for rollback
if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q 'pitun-backend:latest'; then
    docker tag pitun-backend:latest "pitun-backend:rollback-$TS" 2>/dev/null || true
    ok "Image tagged for rollback: pitun-backend:rollback-$TS"
fi

echo

# ── 2. Pull fresh code ───────────────────────────────────────────────────
info "Step 2: Pull fresh code from $REPO/$BRANCH"

TMP_DIR="/tmp/pitun-update-$$"
rm -rf "$TMP_DIR"
git clone --depth 1 --branch "$BRANCH" "https://github.com/${REPO}.git" "$TMP_DIR" 2>&1 | tail -5

if [[ ! -d "$TMP_DIR/backend" ]]; then
    error "Clone failed — aborting"
    exit 1
fi

# Get the commit hash for logging
COMMIT=$(cd "$TMP_DIR" && git rev-parse --short HEAD)
ok "Cloned commit: $COMMIT"

# ── 3. Overlay new files (preserve .env, data/, .git, .venv*) ────────────
info "Step 3: Overlay new files onto $INSTALL_DIR"

# Files/dirs to preserve during overlay
PRESERVE=".env data .git .venv .venv-test node_modules Caddyfile"

# Create a tar of the fresh clone excluding preserved paths
# Then extract on top of the install dir
cd "$TMP_DIR"
# rsync if available, else use tar
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.git' \
        --exclude='.env' \
        --exclude='data/' \
        --exclude='.venv/' \
        --exclude='.venv-test/' \
        --exclude='node_modules/' \
        ./ "$INSTALL_DIR/"
    ok "rsync overlay complete"
else
    # tar-based: create excluding preserved dirs, extract over install
    tar cf - \
        --exclude='.git' \
        --exclude='.env' \
        --exclude='data' \
        --exclude='.venv' \
        --exclude='.venv-test' \
        --exclude='node_modules' \
        . | tar xf - -C "$INSTALL_DIR/"
    ok "tar overlay complete"
fi

# Clean up temp
rm -rf "$TMP_DIR"
echo

# ── 4. Restart backend ──────────────────────────────────────────────────
info "Step 4: Restart backend"
cd "$INSTALL_DIR"

# Check if SECRET_KEY is set (v1.4.7+ guard)
SECRET=$(grep '^SECRET_KEY=' .env 2>/dev/null | cut -d= -f2 || true)
if [[ -z "$SECRET" || "$SECRET" == "changeme" ]]; then
    warn "SECRET_KEY is empty or changeme — generating new one"
    NEW_KEY=$(openssl rand -hex 32)
    if grep -q '^SECRET_KEY=' .env; then
        sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$NEW_KEY|" .env
    else
        echo "SECRET_KEY=$NEW_KEY" >> .env
    fi
    ok "SECRET_KEY generated and saved to .env"
else
    ok "SECRET_KEY already set (non-default)"
fi

# Restart
docker compose restart backend 2>&1 | tail -5
sleep 8

echo

# ── 5. Verify ───────────────────────────────────────────────────────────
info "Step 5: Verify"

CONTAINER_STATUS=$(docker inspect pitun-backend --format '{{.State.Status}}' 2>/dev/null || echo "missing")
if [[ "$CONTAINER_STATUS" != "running" ]]; then
    error "Backend is not running (status: $CONTAINER_STATUS)"
    error "Last 20 log lines:"
    docker logs --tail 20 pitun-backend 2>&1 || true
    echo
    warn "Attempting rollback..."

    # Restore DB
    if [[ -f "/root/pitun-backup-$TS.db" ]]; then
        cp "/root/pitun-backup-$TS.db" "$INSTALL_DIR/data/pitun.db"
    fi
    # Restore .env
    if [[ -f "/root/pitun-backup-$TS.env" ]]; then
        cp "/root/pitun-backup-$TS.env" "$INSTALL_DIR/.env"
    fi
    # Restore image
    docker tag "pitun-backend:rollback-$TS" pitun-backend:latest 2>/dev/null || true

    docker compose restart backend 2>&1 | tail -5
    sleep 5
    warn "Rollback done. Check: docker logs --tail 20 pitun-backend"
    exit 1
fi

ok "Backend is running"

# Health check
HEALTH=$(curl -fsS http://127.0.0.1:8000/health 2>/dev/null || echo "FAILED")
if [[ "$HEALTH" == "FAILED" ]]; then
    warn "Health endpoint not responding yet — checking logs..."
    docker logs --tail 10 pitun-backend 2>&1 | tail -10
    warn "Backend might still be starting. Wait 10s and retry:"
    warn "  curl -fsS http://127.0.0.1:8000/health"
else
    VERSION=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "?")
    ok "Health: $HEALTH"
    ok "Version: $VERSION"
fi

echo
echo "============================================"
ok "Update complete!"
echo "  Backup: /root/pitun-backup-$TS.db"
echo "  Rollback image: pitun-backend:rollback-$TS"
echo "============================================"
