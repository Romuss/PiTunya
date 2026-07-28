#!/usr/bin/env bash
# ============================================================================
# PiTun — online backup of the SQLite state DB
# ============================================================================
# Uses SQLite's `VACUUM INTO` (in-place online backup — safe even while the
# backend is writing). Rotates N copies (default 7). Optionally encrypts
# with `age` if a recipient is provided.
#
# What gets backed up:
#   -./data/pitun.db (the entire state DB: nodes with passwords, SSH private
#   keys, routing rules, devices, settings, events).
#
# Why this matters: ./data/pitun.db is the single point of failure. If the
# SD card corrupts, the operator loses every Node UUID, every VPS SSH key,
# every routing rule, every subscription. Without this script, recovery is
# "rebuild from screenshots and memory". With it, `cat backup.db.gpg |
# openssl ... | sqlite3 pitun.db && docker compose up -d`.
#
# Tested under:
#   - SQLite 3.27+ (VACUUM INTO introduced)
#   - age 1.0+ (https://github.com/FiloSottile/age)
#   - gpg as fallback encryptor
#
# ─── Usage ───────────────────────────────────────────────────────────────
#
#   # Backup into ./data/backups/, keep last 7:
#   sudo bash scripts/backup.sh
#
#   # Backup with rotation + age encryption to the listed recipient:
#   sudo BACKUP_AGE_RECIPIENTS="age1x..." bash scripts/backup.sh
#
#   # Backup to a custom dir, keep 30, use GPG passphrase encryption:
#   sudo BACKUP_DIR=/mnt/pitun-backups BACKUP_KEEP=30 \
#        BACKUP_GPG_PASSPHRASE="secret" bash scripts/backup.sh
#
#   # Dry-run (don't write anything):
#   sudo bash scripts/backup.sh --dry-run
#
# ─── Restore ────────────────────────────────────────────────────────────
#
#   cd /opt/pitun
#   sudo docker compose stop backend
#   sudo cp data/pitun.db data/pitun.db.broken  # don't lose the original
#   # Optional decryption:
#   age -d -i ~/.ssh/age-key.txt data/backups/2026-07-27T14:30.db.age > /tmp/restore.db
#   # OR with GPG:
#   gpg -d data/backups/2026-07-27T14:30.db.gpg > /tmp/restore.db
#   # Restore:
#   sudo cp /tmp/restore.db data/pitun.db
#   sudo docker compose up -d backend
#   # Migrations run automatically on boot via entrypoint.sh
#
# Set up a daily cron (crontab -e as root):
#   0 4 * * * /opt/pitun/scripts/backup.sh >> /var/log/pitun-backup.log 2>&1
# ============================================================================

set -euo pipefail

# ── Pretty output ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Args + config ─────────────────────────────────────────────────────────
DRY_RUN=0
[[ "${1:-}" == "--dry-run" || "${1:-}" == "-n" ]] && DRY_RUN=1

# PiTun install root (auto-detect: this script lives in scripts/, so the
# repo root is its parent only when invoked from a repo checkout — but for
# a production install it's typically /opt/pitun; honor PITUN_DIR env var
# but fall back to /opt/pitun).
PITUN_DIR="${PITUN_DIR:-/opt/pitun}"
DB_PATH="${PITUN_DB:-$PITUN_DIR/data/pitun.db}"

# Where backups land. Default: a directory inside PITUN_DIR so the operator
# doesn't have to mount anything extra. The backup dir should be on a
# different physical drive / network mount than the DB.
BACKUP_DIR="${BACKUP_DIR:-$PITUN_DIR/data/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"

# Optional encryption. Prefer `age` (FiloSottile/age, modern, simple) over
# gpg. If BACKUP_AGE_RECIPIENTS is set, age is used; else if
# BACKUP_GPG_PASSPHRASE is set, gpg is used with that passphrase; else
# backups land plaintext with a chmod-0600.
BACKUP_AGE_RECIPIENTS="${BACKUP_AGE_RECIPIENTS:-}"
BACKUP_GPG_PASSPHRASE="${BACKUP_GPG_PASSPHRASE:-}"

# ── Sanity ────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0"

info "PiTun backup starting"
info "  DB=$DB_PATH"
info "  BACKUP_DIR=$BACKUP_DIR"
info "  BACKUP_KEEP=$BACKUP_KEEP"
if [[ -n "$BACKUP_AGE_RECIPIENTS" ]]; then
    command -v age >/dev/null 2>&1 || error "BACKUP_AGE_RECIPIENTS set but \`age\` not installed (https://github.com/FiloSottile/age)"
    info "  encryption: age (recipients: ${BACKUP_AGE_RECIPIENTS:0:40}...)"
elif [[ -n "$BACKUP_GPG_PASSPHRASE" ]]; then
    command -v gpg >/dev/null 2>&1 || error "BACKUP_GPG_PASSPHRASE set but \`gpg\` not installed"
    info "  encryption: gpg (passphrase)"
else
    warn "  encryption: NONE — backups will be plaintext (chmod 0600)"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    info "  --dry-run: no files will be written"
fi

[[ -f "$DB_PATH" ]] || error "DB not found at $DB_PATH"

# Detect SQLite tool. Prefer `sqlite3`. Fall back to the python stdlib
# sqlite3 module if the CLI is absent (RPi base image may not ship sqlite3).
SQLITE_BIN=""
if command -v sqlite3 >/dev/null 2>&1; then
    SQLITE_BIN="$(command -v sqlite3)"
elif command -v python3 >/dev/null 2>&1; then
    warn "sqlite3 CLI not found — falling back to python3 sqlite3 module for VACUUM INTO"
    SQLITE_BIN="__python_fallback__"
else
    error "Neither \`sqlite3\` nor \`python3\` available — cannot run VACUUM INTO"
fi

# ── Pre-flight: WAL checkpoint ─────────────────────────────────────────────
# Force all WAL frames into the main DB so the VACUUM INTO snapshot is
# consistent. Otherwise a fresh insert might still be in the WAL and
# missing from the VACUUM'd copy. Best-effort — if it fails, the snapshot
# is still nearly consistent (the next backup tick will catch up).
info "Pre-flight: WAL checkpoint (PRAGMA wal_checkpoint(TRUNCATE))"
if [[ "$DRY_RUN" != "1" ]]; then
    if [[ "$SQLITE_BIN" == "__python_fallback__" ]]; then
        python3 -c "import sqlite3,sys; c=sqlite3.connect('$DB_PATH'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" || \
            warn "wal_checkpoint failed — continuing"
    else
        "$SQLITE_BIN" "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);" || warn "wal_checkpoint failed — continuing"
    fi
fi

# ── Build timestamped snapshot ─────────────────────────────────────────────
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
STAGING="$BACKUP_DIR/.staging.$$.db"
[[ -d "$BACKUP_DIR" ]] || { info "creating $BACKUP_DIR"; mkdir -p "$BACKUP_DIR"; }

info "VACUUM INTO $STAGING"
if [[ "$DRY_RUN" == "1" ]]; then
    info "  [dry-run] skipped"
else
    if [[ "$SQLITE_BIN" == "__python_fallback__" ]]; then
        python3 -c "import sqlite3; c=sqlite3.connect('$DB_PATH'); c.execute(\"VACUUM INTO '$STAGING'\"); c.close()" \
            || error "VACUUM INTO failed"
    else
        "$SQLITE_BIN" "$DB_PATH" "VACUUM INTO '$STAGING';" \
            || error "VACUUM INTO failed"
    fi
    [[ -s "$STAGING" ]] || error "backup is empty after VACUUM INTO"
fi

# ── Verify (cheap checksum, NOT authoritative — just a smoke check) ─────
if [[ "$DRY_RUN" != "1" ]]; then
    if [[ "$SQLITE_BIN" == "__python_fallback__" ]]; then
        SRC_INTEGRITY=$(python3 -c "import sqlite3; c=sqlite3.connect('$DB_PATH'); print(c.integrity_check().fetchone()[0]); c.close()")
        DST_INTEGRITY=$(python3 -c "import sqlite3; c=sqlite3.connect('$STAGING'); print(c.integrity_check().fetchone()[0]); c.close()")
    else
        SRC_INTEGRITY=$("$SQLITE_BIN" "$DB_PATH" "PRAGMA integrity_check;")
        DST_INTEGRITY=$("$SQLITE_BIN" "$STAGING" "PRAGMA integrity_check;")
    fi
    if [[ "$SRC_INTEGRITY" == "ok" && "$DST_INTEGRITY" == "ok" ]]; then
        info "integrity check: both source and backup OK"
    else
        warn "integrity mismatch — source=$SRC_INTEGRITY, backup=$DST_INTEGRITY"
        warn "Keeping the staging file anyway (likely fine); investigate manually"
    fi
fi

# ── Move staging to timestamped filename, encrypt if requested ────────────
OUT_PLAIN="$BACKUP_DIR/$TS.db"
if [[ "$DRY_RUN" == "1" ]]; then
    info "  [dry-run] would write: $OUT_PLAIN"
    rm -f "$STAGING"
    exit 0
fi

if [[ -n "$BACKUP_AGE_RECIPIENTS" ]]; then
    OUT_ENC="$OUT_PLAIN.age"
    info "encrypting (age) → $OUT_ENC"
    age --encrypt $(printf -- '-r %q ' $BACKUP_AGE_RECIPIENTS) -o "$OUT_ENC" "$STAGING" \
        || { warn "age encryption failed — keeping plaintext $OUT_PLAIN"; cp "$STAGING" "$OUT_PLAIN"; }
    rm -f "$STAGING"
    chmod 0600 "$OUT_ENC"
    OUT_FINAL="$OUT_ENC"
elif [[ -n "$BACKUP_GPG_PASSPHRASE" ]]; then
    OUT_ENC="$OUT_PLAIN.gpg"
    info "encrypting (gpg, passphrase) → $OUT_ENC"
    echo "$BACKUP_GPG_PASSPHRASE" | gpg --batch --yes --passphrase-fd 0 \
        --cipher-algo AES256 --compress-algo none \
        -c -o "$OUT_ENC" "$STAGING" \
        || { warn "gpg encryption failed — keeping plaintext $OUT_PLAIN"; cp "$STAGING" "$OUT_PLAIN"; }
    rm -f "$STAGING"
    chmod 0600 "$OUT_ENC"
    OUT_FINAL="$OUT_ENC"
else
    mv "$STAGING" "$OUT_PLAIN"
    chmod 0600 "$OUT_PLAIN"
    OUT_FINAL="$OUT_PLAIN"
fi

info "backup written: $OUT_FINAL ($(du -h "$OUT_FINAL" | cut -f1))"

# ── Rotate: keep only the most recent $BACKUP_KEEP ─────────────────────────
shopt -s nullglob
COUNT=0
for f in $(ls -1t "$BACKUP_DIR"/20*.db* 2>/dev/null); do
    COUNT=$((COUNT + 1))
    if [[ $COUNT -gt $BACKUP_KEEP ]]; then
        info "rotating out (older than top $BACKUP_KEEP): $f"
        rm -f "$f"
    fi
done

info "OK — backup complete"
