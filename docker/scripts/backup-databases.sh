#!/bin/bash
# backup-databases.sh — Daily SQLite backup with compression & rotation
# Cron: 0 3 * * * sudo /home/ubuntu/RLD/docker/scripts/backup-databases.sh
#
# Uses VACUUM INTO for hot backups (safe while DB is in use).
# Compresses with gzip, retains 7 days, logs results.

set -euo pipefail

# ── Config ──
BACKUP_ROOT="/home/ubuntu/RLD/backups"
RETENTION_DAYS=7
DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$BACKUP_ROOT/$DATE"
LOG="$BACKUP_ROOT/backup.log"
STATUS_FILE="$BACKUP_ROOT/last_backup.json"

# Databases to back up: name|container|path
DATABASES=(
  "aave_rates|docker-rates-indexer-1|/app/data/aave_rates.db"
  "clean_rates|docker-rates-indexer-1|/app/data/clean_rates.db"
)

# ── Functions ──
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

backup_db() {
  local name="$1" container="$2" db_path="$3"
  local out_file="$BACKUP_DIR/${name}.db"
  local gz_file="${out_file}.gz"

  log "  Backing up $name from $container:$db_path ..."

  # Check container is running
  if ! docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
    log "  ⚠️  Container $container not running, trying direct volume access..."
    # Try direct file copy as fallback
    local vol_path
    vol_path=$(docker inspect "$container" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || echo "")
    if [ -n "$vol_path" ] && [ -f "$vol_path/$(basename "$db_path")" ]; then
      cp "$vol_path/$(basename "$db_path")" "$out_file"
    else
      log "  ❌ FAILED: Cannot access $name"
      return 1
    fi
  else
    # VACUUM INTO creates a consistent snapshot without locking the DB
    docker exec "$container" python3 -c "
import sqlite3
conn = sqlite3.connect('$db_path')
conn.execute(\"VACUUM INTO '/tmp/backup_${name}.db'\")
conn.close()
" 2>>"$LOG"

    # Copy from container
    docker cp "$container:/tmp/backup_${name}.db" "$out_file" 2>>"$LOG"

    # Clean up temp file in container
    docker exec "$container" rm -f "/tmp/backup_${name}.db" 2>/dev/null || true
  fi

  # Verify integrity
  if ! python3 -c "import sqlite3; c=sqlite3.connect('$out_file'); r=c.execute('PRAGMA integrity_check').fetchone()[0]; c.close(); exit(0 if r=='ok' else 1)" 2>/dev/null; then
    log "  ❌ INTEGRITY FAIL: $name"
    rm -f "$out_file"
    return 1
  fi

  # Compress
  gzip -f "$out_file"
  local size
  size=$(du -sh "$gz_file" | awk '{print $1}')
  log "  ✅ $name → $gz_file ($size)"
  return 0
}

# ── Main ──
mkdir -p "$BACKUP_ROOT"
log "═══ Starting daily backup ═══"
mkdir -p "$BACKUP_DIR"

total=0
success=0
failed_list=""

for entry in "${DATABASES[@]}"; do
  IFS='|' read -r name container db_path <<< "$entry"
  total=$((total + 1))
  if backup_db "$name" "$container" "$db_path"; then
    success=$((success + 1))
  else
    failed_list="$failed_list $name"
  fi
done

# ── Rotation: delete backups older than RETENTION_DAYS ──
deleted=0
if [ -d "$BACKUP_ROOT" ]; then
  while IFS= read -r old_dir; do
    [ -z "$old_dir" ] && continue
    rm -rf "$old_dir"
    deleted=$((deleted + 1))
    log "  🗑️  Removed old backup: $(basename "$old_dir")"
  done < <(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +$RETENTION_DAYS 2>/dev/null)
fi

# ── Summary ──
backup_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | awk '{print $1}' || echo "0")
status="success"
[ "$success" -lt "$total" ] && status="partial"
[ "$success" -eq 0 ] && status="failed"

log "═══ Backup complete: $success/$total OK, ${deleted} old removed, size: $backup_size ═══"

# Write status file for dashboard integration
cat > "$STATUS_FILE" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "date": "$DATE",
  "status": "$status",
  "total": $total,
  "success": $success,
  "failed": [$(echo "$failed_list" | sed 's/^ //;s/ /","/g;s/^/"&/;s/$/&"/' | sed 's/^""$//')],
  "size": "$backup_size",
  "retained": $(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l),
  "deleted": $deleted
}
EOF
