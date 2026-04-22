#!/bin/bash
# backup-databases.sh — Daily backup of simulation indexer Postgres snapshots.
# Cron: 0 3 * * * sudo /home/ubuntu/RLD/docker/scripts/backup-databases.sh

set -euo pipefail

BACKUP_ROOT="/home/ubuntu/RLD/backups"
RETENTION_DAYS=7
DATE=$(date +%Y-%m-%d)
BACKUP_DIR="$BACKUP_ROOT/$DATE"
LOG="$BACKUP_ROOT/backup.log"
STATUS_FILE="$BACKUP_ROOT/last_backup.json"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

resolve_container() {
  local service="$1"
  docker ps --filter "label=com.docker.compose.service=${service}" --format '{{.Names}}' | head -1
}

backup_postgres() {
  local service="$1"
  local db_name="$2"
  local user="$3"
  local out_file="$BACKUP_DIR/${db_name}.sql"
  local gz_file="${out_file}.gz"

  local container=""
  container=$(resolve_container "$service" || true)
  if [ -z "$container" ]; then
    log "  ❌ FAILED: service ${service} not running"
    return 1
  fi

  log "  Backing up PostgreSQL database ${db_name} from ${container} ..."
  if ! docker exec "$container" pg_dump -U "$user" "$db_name" > "$out_file" 2>>"$LOG"; then
    log "  ❌ FAILED: pg_dump for ${db_name}"
    rm -f "$out_file"
    return 1
  fi
  if [ ! -s "$out_file" ]; then
    log "  ❌ FAILED: empty backup output for ${db_name}"
    rm -f "$out_file"
    return 1
  fi

  gzip -f "$out_file"
  local size
  size=$(du -sh "$gz_file" | awk '{print $1}')
  log "  ✅ ${db_name} → ${gz_file} (${size})"
  return 0
}

backup_clickhouse_schema() {
  local service="$1"
  local out_file="$BACKUP_DIR/clickhouse_schema.sql"
  local gz_file="${out_file}.gz"

  local container=""
  container=$(resolve_container "$service" || true)
  if [ -z "$container" ]; then
    log "  ❌ FAILED: service ${service} not running"
    return 1
  fi

  log "  Backing up ClickHouse schema from ${container} ..."
  if ! docker exec "$container" clickhouse-client -q "
SELECT concat('/* ', database, '.', name, ' */\n', create_table_query, ';\n')
FROM system.tables
WHERE database NOT IN ('system', 'information_schema', 'INFORMATION_SCHEMA')
ORDER BY database, name
FORMAT TSVRaw
" > "$out_file" 2>>"$LOG"; then
    log "  ❌ FAILED: clickhouse schema export"
    rm -f "$out_file"
    return 1
  fi
  if [ ! -s "$out_file" ]; then
    log "  ❌ FAILED: empty ClickHouse schema export"
    rm -f "$out_file"
    return 1
  fi

  gzip -f "$out_file"
  local size
  size=$(du -sh "$gz_file" | awk '{print $1}')
  log "  ✅ ClickHouse schema → ${gz_file} (${size})"
  return 0
}

mkdir -p "$BACKUP_ROOT" "$BACKUP_DIR"
log "═══ Starting daily backup ═══"

total=2
success=0
failed_list=""

if backup_postgres "postgres" "rld_indexer" "rld"; then
  success=$((success + 1))
else
  failed_list="rld_indexer"
fi

if backup_clickhouse_schema "clickhouse"; then
  success=$((success + 1))
else
  if [ -n "$failed_list" ]; then
    failed_list="$failed_list clickhouse_schema"
  else
    failed_list="clickhouse_schema"
  fi
fi

deleted=0
if [ -d "$BACKUP_ROOT" ]; then
  while IFS= read -r old_dir; do
    [ -z "$old_dir" ] && continue
    rm -rf "$old_dir"
    deleted=$((deleted + 1))
    log "  🗑️  Removed old backup: $(basename "$old_dir")"
  done < <(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +$RETENTION_DAYS 2>/dev/null)
fi

backup_size=$(du -sh "$BACKUP_DIR" 2>/dev/null | awk '{print $1}' || echo "0")
status="success"
[ "$success" -lt "$total" ] && status="partial"
[ "$success" -eq 0 ] && status="failed"

log "═══ Backup complete: $success/$total OK, ${deleted} old removed, size: $backup_size ═══"

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
