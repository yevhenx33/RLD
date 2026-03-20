#!/bin/bash
# generate-status.sh — Collects comprehensive system metrics → status.json
# Cron: * * * * * sudo /home/ubuntu/RLD/docker/scripts/generate-status.sh

set -euo pipefail

OUTPUT="/home/ubuntu/RLD/docker/dashboard/status.json"
HISTORY="/home/ubuntu/RLD/docker/dashboard/history.json"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── System ──
DISK_TOTAL=$(df -BG / | awk 'NR==2{print $2}' | tr -d 'G')
DISK_USED=$(df -BG / | awk 'NR==2{print $3}' | tr -d 'G')
DISK_FREE=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
DISK_PCT=$(df / | awk 'NR==2{print $5}' | tr -d '%')
MEM_TOTAL=$(free -m | awk '/Mem/{print $2}')
MEM_USED=$(free -m | awk '/Mem/{print $3}')
MEM_AVAIL=$(free -m | awk '/Mem/{print $7}')
LOAD_1=$(cat /proc/loadavg | awk '{print $1}')
LOAD_5=$(cat /proc/loadavg | awk '{print $2}')
LOAD_15=$(cat /proc/loadavg | awk '{print $3}')
UPTIME_SECS=$(awk '{print int($1)}' /proc/uptime)
CPU_CORES=$(nproc)
SWAP_TOTAL=$(free -m | awk '/Swap/{print $2}')
SWAP_USED=$(free -m | awk '/Swap/{print $3}')

# ── Containers with stats ──
containers_json="["
first=true
while IFS='|' read -r name cpu mem netio; do
  [ -z "$name" ] && continue
  name_clean=$(echo "$name" | xargs)
  cpu_clean=$(echo "$cpu" | xargs | tr -d '%')
  mem_clean=$(echo "$mem" | xargs)
  net_clean=$(echo "$netio" | xargs | sed 's/"/\\"/g')
  
  # Get status + restart count
  status_line=$(docker ps -a --filter "name=$name_clean" --format "{{.Status}}" 2>/dev/null | head -1)
  restart_count=$(docker inspect --format '{{.RestartCount}}' "$name_clean" 2>/dev/null || echo "0")
  started_at=$(docker inspect --format '{{.State.StartedAt}}' "$name_clean" 2>/dev/null | cut -d'.' -f1 || echo "")
  
  health="running"
  if echo "$status_line" | grep -q "(healthy)"; then health="healthy"
  elif echo "$status_line" | grep -q "(unhealthy)"; then health="unhealthy"
  elif echo "$status_line" | grep -q "Exited"; then health="stopped"; fi
  
  uptime_str=$(echo "$status_line" | sed 's/ (healthy)//' | sed 's/ (unhealthy)//')
  ports=$(docker ps --filter "name=$name_clean" --format "{{.Ports}}" 2>/dev/null | head -1 | sed 's/"/\\"/g')

  if [ "$first" = true ]; then first=false; else containers_json+=","; fi
  containers_json+="{\"name\":\"$name_clean\",\"status\":\"$health\",\"uptime\":\"$uptime_str\",\"cpu\":$cpu_clean,\"memory\":\"$mem_clean\",\"network\":\"$net_clean\",\"restarts\":$restart_count,\"started\":\"$started_at\",\"ports\":\"$ports\"}"
done < <(docker stats --no-stream --format "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}" 2>/dev/null || true)

# Add stopped containers
while IFS='|' read -r name status_line; do
  [ -z "$name" ] && continue
  name_clean=$(echo "$name" | xargs)
  # Skip if already in list
  echo "$containers_json" | grep -q "\"$name_clean\"" && continue
  uptime_str=$(echo "$status_line" | xargs)
  if [ "$first" = true ]; then first=false; else containers_json+=","; fi
  containers_json+="{\"name\":\"$name_clean\",\"status\":\"stopped\",\"uptime\":\"$uptime_str\",\"cpu\":0,\"memory\":\"0B / 0B\",\"network\":\"0B / 0B\",\"restarts\":0,\"started\":\"\",\"ports\":\"\"}"
done < <(docker ps -a --filter "status=exited" --format "{{.Names}}|{{.Status}}" 2>/dev/null || true)

# Add containers in 'created' state (stuck after deployer)
while IFS='|' read -r name status_line; do
  [ -z "$name" ] && continue
  name_clean=$(echo "$name" | xargs)
  echo "$containers_json" | grep -q "\"$name_clean\"" && continue
  uptime_str=$(echo "$status_line" | xargs)
  if [ "$first" = true ]; then first=false; else containers_json+=","; fi
  containers_json+="{\"name\":\"$name_clean\",\"status\":\"created\",\"uptime\":\"$uptime_str\",\"cpu\":0,\"memory\":\"0B / 0B\",\"network\":\"0B / 0B\",\"restarts\":0,\"started\":\"\",\"ports\":\"\"}"
done < <(docker ps -a --filter "status=created" --format "{{.Names}}|{{.Status}}" 2>/dev/null || true)
containers_json+="]"

# ── API health + response times ──
rates_rt=$(curl -so /dev/null -w "%{time_total}" -m 3 http://localhost:8081/ 2>/dev/null || echo "-1")
indexer_rt=$(curl -sf -o /dev/null -w "%{time_total}" -m 3 http://localhost:8080/healthz 2>/dev/null) || indexer_rt="-1"
bot_rt=$(curl -so /dev/null -w "%{time_total}" -m 3 http://localhost:8082/health 2>/dev/null || echo "-1")
nginx_rt=$(curl -so /dev/null -w "%{time_total}" -m 3 https://rld.fi/ 2>/dev/null || echo "-1")

rates_ok=$([ "$rates_rt" != "-1" ] && echo "true" || echo "false")
indexer_ok=$([ "$indexer_rt" != "-1" ] && echo "true" || echo "false")
bot_ok=$([ "$bot_rt" != "-1" ] && echo "true" || echo "false")
nginx_ok=$([ "$nginx_rt" != "-1" ] && echo "true" || echo "false")

rates_block=$(curl -sf -m 2 http://localhost:8081/ 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_indexed_block',''))" 2>/dev/null || echo "")

# ── Reth (sim chain) ──
anvil_ok="false"
anvil_block=""
reth_mem_mb=0
reth_uptime=""
reth_db_mb=0
reth_chain_id=""
reth_gas_price=""
reth_block_ts=0
reth_txpool_pending=0
reth_txpool_queued=0

# Single JSON-RPC batch call (replaces 5 sequential curls)
reth_batch=$(curl -sf -m 3 -X POST -H "Content-Type: application/json" -d '[
  {"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1},
  {"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":2},
  {"jsonrpc":"2.0","method":"eth_gasPrice","params":[],"id":3},
  {"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest",false],"id":4},
  {"jsonrpc":"2.0","method":"txpool_status","params":[],"id":5}
]' http://localhost:8545 2>/dev/null || true)

if [ -n "$reth_batch" ]; then
  anvil_ok="true"
  eval $(echo "$reth_batch" | python3 -c "
import sys, json
try:
    results = {r['id']: r.get('result') for r in json.load(sys.stdin)}
    block = int(results[1], 16) if results.get(1) else 0
    chain = int(results[2], 16) if results.get(2) else 0
    gas = round(int(results[3], 16) / 1e9, 2) if results.get(3) else 0
    blk = results.get(4, {})
    ts = int(blk['timestamp'], 16) if blk and 'timestamp' in blk else 0
    tp = results.get(5, {})
    pending = int(tp.get('pending', '0x0'), 16) if tp else 0
    queued = int(tp.get('queued', '0x0'), 16) if tp else 0
    print(f'anvil_block={block}')
    print(f'reth_chain_id={chain}')
    print(f'reth_gas_price={gas}')
    print(f'reth_block_ts={ts}')
    print(f'reth_txpool_pending={pending}')
    print(f'reth_txpool_queued={queued}')
except:
    print('anvil_block=0')
" 2>/dev/null)

  # Process metrics (bare-metal, no network calls)
  reth_pid=$(pgrep -x reth 2>/dev/null || echo "")
  if [ -n "$reth_pid" ]; then
    reth_mem_mb=$(ps -p "$reth_pid" -o rss --no-headers 2>/dev/null | awk '{printf "%.0f", $1/1024}' || echo 0)
    reth_uptime=$(ps -p "$reth_pid" -o etime --no-headers 2>/dev/null | xargs || echo "")
  fi

  # DB size (local, fast)
  reth_db_mb=$(du -sm /home/ubuntu/.local/share/reth-dev/ 2>/dev/null | awk '{print $1}' || echo 0)
fi

# ── SSL (read cert file directly — instant and deterministic) ──
SSL_EXPIRY="unknown"; SSL_DAYS="0"
CERT_FILE="/etc/letsencrypt/live/rld.fi/fullchain.pem"
if [ -f "$CERT_FILE" ]; then
  CERT_END=$(openssl x509 -enddate -noout -in "$CERT_FILE" 2>/dev/null | cut -d= -f2)
  if [ -n "$CERT_END" ]; then
    CERT_EPOCH=$(date -d "$CERT_END" +%s 2>/dev/null)
    NOW_EPOCH=$(date +%s)
    if [ -n "$CERT_EPOCH" ]; then
      SSL_DAYS=$(( (CERT_EPOCH - NOW_EPOCH) / 86400 ))
      SSL_EXPIRY=$(date -d "$CERT_END" +%Y-%m-%d 2>/dev/null || echo "unknown")
      [ "$SSL_DAYS" -lt 0 ] && SSL_DAYS=0
    fi
  fi
fi

# ── Nginx ──
if sudo nginx -t >/dev/null 2>&1; then nginx_conf_ok="true"; else nginx_conf_ok="false"; fi

# ── Git ──
GIT_COMMIT=$(cd /home/ubuntu/RLD && git log -1 --format="%h" 2>/dev/null || echo "?")
GIT_MSG=$(cd /home/ubuntu/RLD && git log -1 --format="%s" 2>/dev/null | head -c 80 | sed 's/"/\\"/g' || echo "?")
GIT_TIME=$(cd /home/ubuntu/RLD && git log -1 --format="%cI" 2>/dev/null || echo "")
GIT_AUTHOR=$(cd /home/ubuntu/RLD && git log -1 --format="%an" 2>/dev/null || echo "?")

# ── Docker ──
DANGLING=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l)
IMG_SIZE=$(docker system df --format "{{.Size}}" 2>/dev/null | head -1 || echo "0")
IMG_ACTIVE=$(docker images --filter "dangling=false" -q 2>/dev/null | wc -l)
IMG_TOTAL=$(docker images -q 2>/dev/null | wc -l)

# ── Network connections ──
ESTAB=$(ss -t state established 2>/dev/null | wc -l)
LISTEN=$(ss -tlnp 2>/dev/null | tail -n +2 | wc -l)

# ── Recent errors from logs ──
ERR_COUNT=0
if ls /home/ubuntu/RLD/logs/*$(date +%Y-%m-%d)*.log >/dev/null 2>&1; then
  _ec=$(cat /home/ubuntu/RLD/logs/*$(date +%Y-%m-%d)*.log 2>/dev/null | grep -ic "error\|exception\|traceback\|fatal" 2>/dev/null) && ERR_COUNT=$_ec || ERR_COUNT=0
fi

# ── Backups ──
BACKUP_JSON=$(cat /home/ubuntu/RLD/backups/last_backup.json 2>/dev/null || echo '{"status":"never","timestamp":"never","size":"0","retained":0}')

# ── Database Integrity ──
DB_JSON=$(docker exec docker-rates-indexer-1 python3 -c "
import sqlite3, os, json, time

now = int(time.time())
result = {}

# --- aave_rates.db ---
try:
    db = '/app/data/aave_rates.db'
    sz = round(os.path.getsize(db) / 1024 / 1024, 1)
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    c = conn.cursor()
    tables = {}
    for t in ['rates', 'eth_prices', 'rates_dai', 'rates_usdt']:
        try:
            c.execute(f'SELECT COUNT(*) FROM {t}')
            cnt = c.fetchone()[0]
            c.execute(f'SELECT MAX(timestamp) FROM {t}')
            mx = c.fetchone()[0] or 0
            tables[t] = {'rows': cnt, 'latest_age_secs': now - mx if mx > 1000000 else -1}
        except: pass
    c.execute('SELECT COUNT(*) FROM eth_prices WHERE timestamp < 100000000')
    corrupt = c.fetchone()[0]
    conn.close()
    result['aave_rates'] = {'size_mb': sz, 'tables': tables, 'corrupt_rows': corrupt}
except Exception as e:
    result['aave_rates'] = {'error': str(e)}

# --- clean_rates.db ---
try:
    db2 = '/app/data/clean_rates.db'
    sz2 = round(os.path.getsize(db2) / 1024 / 1024, 1)
    conn2 = sqlite3.connect(f'file:{db2}?mode=ro', uri=True)
    c2 = conn2.cursor()
    c2.execute('SELECT MAX(timestamp) FROM hourly_stats')
    mx2 = c2.fetchone()[0] or 0
    fresh = now - mx2 if mx2 > 0 else -1
    nulls = {}
    for col in ['eth_price', 'usdc_rate', 'dai_rate', 'usdt_rate']:
        c2.execute(f'SELECT COUNT(*) FROM hourly_stats WHERE timestamp > ? AND {col} IS NULL', (now - 7*86400,))
        nulls[col] = c2.fetchone()[0]
    c2.execute('SELECT COUNT(*) FROM hourly_stats WHERE timestamp > ?', (now - 7*86400,))
    rows_7d = c2.fetchone()[0]
    c2.execute('SELECT COUNT(*) FROM hourly_stats WHERE timestamp > ?', (now - 86400,))
    rows_24h = c2.fetchone()[0]
    # Sync age
    sync_age = -1
    try:
        c2.execute(\"SELECT value FROM sync_state WHERE key='last_synced_timestamp'\")
        r = c2.fetchone()
        if r: sync_age = now - int(r[0])
    except: pass
    conn2.close()
    result['clean_rates'] = {
        'size_mb': sz2, 'freshness_secs': fresh, 'rows_24h': rows_24h,
        'nulls_7d': nulls, 'missing_hours_7d': max(0, 168 - rows_7d),
        'sync_age_secs': sync_age
    }
except Exception as e:
    result['clean_rates'] = {'error': str(e)}

print(json.dumps(result))
" 2>/dev/null) || DB_JSON='{}'

# --- Pool state from rld_indexer postgres ---
# Try reth-postgres-1 (Reth mode) first, fallback to docker-postgres-1 (Anvil mode)
PG_CONTAINER=""
for pg_name in reth-postgres-1 docker-postgres-1; do
  if docker inspect "$pg_name" >/dev/null 2>&1; then
    PG_CONTAINER="$pg_name"
    break
  fi
done
if [ -n "$PG_CONTAINER" ]; then
POOL_JSON=$(docker exec "$PG_CONTAINER" psql -U rld -d rld_indexer -t -A -c "
SELECT json_build_object(
  'healthy', true,
  'last_indexed_block', COALESCE((SELECT last_indexed_block FROM indexer_state LIMIT 1), 0),
  'total_events', COALESCE((SELECT total_events FROM indexer_state LIMIT 1), 0),
  'block_states_rows', (SELECT COUNT(*) FROM block_states),
  'events_rows', (SELECT COUNT(*) FROM events),
  'mark_price', (SELECT ROUND(mark_price::numeric, 6) FROM block_states WHERE mark_price IS NOT NULL ORDER BY block_number DESC LIMIT 1),
  'index_price', (SELECT ROUND(index_price::numeric, 6) FROM block_states WHERE index_price IS NOT NULL ORDER BY block_number DESC LIMIT 1),
  'liquidity', (SELECT liquidity FROM block_states WHERE liquidity IS NOT NULL ORDER BY block_number DESC LIMIT 1),
  'token0_balance', (SELECT token0_balance FROM block_states WHERE token0_balance IS NOT NULL ORDER BY block_number DESC LIMIT 1),
  'token1_balance', (SELECT token1_balance FROM block_states WHERE token1_balance IS NOT NULL ORDER BY block_number DESC LIMIT 1),
  'total_debt', (SELECT total_debt FROM block_states WHERE total_debt IS NOT NULL ORDER BY block_number DESC LIMIT 1)
);
" 2>/dev/null) || POOL_JSON='{"healthy":false}'
else
  POOL_JSON='{"healthy":false}'
fi

# Merge rates + pool state
DB_JSON=$(python3 -c "
import json, sys
rates = json.loads('''$DB_JSON''') if '''$DB_JSON'''.strip() else {}
pool = json.loads('''$POOL_JSON''') if '''$POOL_JSON'''.strip() else {'healthy': False}
rates['pool_state'] = pool
print(json.dumps(rates))
" 2>/dev/null) || DB_JSON='{}'

# ── History tracking (keep last 60 data points = ~1 hour) ──
if [ -f "$HISTORY" ]; then
  HIST=$(python3 -c "
import json,sys
try:
  h=json.load(open('$HISTORY'))
  h['load'].append($LOAD_1)
  h['mem'].append(round($MEM_USED/$MEM_TOTAL*100,1))
  h['disk'].append($DISK_PCT)
  h['block'].append(${rates_block:-0})
  for k in h: h[k]=h[k][-60:]
  json.dump(h,sys.stdout)
except: json.dump({'load':[$LOAD_1],'mem':[round($MEM_USED/$MEM_TOTAL*100,1)],'disk':[$DISK_PCT],'block':[${rates_block:-0}]},sys.stdout)
" 2>/dev/null)
else
  HIST="{\"load\":[$LOAD_1],\"mem\":[$(python3 -c "print(round($MEM_USED/$MEM_TOTAL*100,1))")],\"disk\":[$DISK_PCT],\"block\":[${rates_block:-0}]}"
fi
echo "$HIST" > "$HISTORY"

# ── Write JSON (atomic) ──
TMPOUT=$(mktemp "${OUTPUT}.XXXXXX")
cat > "$TMPOUT" << ENDJSON
{
  "timestamp": "$TIMESTAMP",
  "system": {
    "uptime_secs": $UPTIME_SECS,
    "load": [$LOAD_1, $LOAD_5, $LOAD_15],
    "cpu_cores": $CPU_CORES,
    "disk": {"total_gb":$DISK_TOTAL,"used_gb":$DISK_USED,"free_gb":$DISK_FREE,"percent":$DISK_PCT},
    "memory": {"total_mb":$MEM_TOTAL,"used_mb":$MEM_USED,"available_mb":$MEM_AVAIL},
    "swap": {"total_mb":$SWAP_TOTAL,"used_mb":$SWAP_USED},
    "connections": {"established":$ESTAB,"listening":$LISTEN},
    "errors_today": $ERR_COUNT
  },
  "containers": $containers_json,
  "services": {
    "nginx": {"healthy":$nginx_conf_ok,"response_ms":$(python3 -c "print(int(float('${nginx_rt}')*1000))" 2>/dev/null || echo -1)},
    "rates_indexer": {"healthy":$rates_ok,"response_ms":$(python3 -c "print(int(float('${rates_rt}')*1000))" 2>/dev/null || echo -1),"last_block":"$rates_block"},
    "indexer": {"healthy":$indexer_ok,"response_ms":$(python3 -c "print(int(float('${indexer_rt}')*1000))" 2>/dev/null || echo -1)},
    "monitor_bot": {"healthy":$bot_ok,"response_ms":$(python3 -c "print(int(float('${bot_rt}')*1000))" 2>/dev/null || echo -1)},
    "anvil": {"healthy":$anvil_ok,"block":"$anvil_block","mem_mb":${reth_mem_mb:-0},"uptime":"$reth_uptime","db_mb":${reth_db_mb:-0},"chain_id":"${reth_chain_id}","gas_gwei":"${reth_gas_price}","block_ts":${reth_block_ts:-0},"txpool_pending":${reth_txpool_pending:-0},"txpool_queued":${reth_txpool_queued:-0}}
  },
  "ssl": {"expiry":"$SSL_EXPIRY","days_remaining":${SSL_DAYS:-0}},
  "git": {"commit":"$GIT_COMMIT","message":"$GIT_MSG","time":"$GIT_TIME","author":"$GIT_AUTHOR"},
  "docker": {"dangling_images":$DANGLING,"images_size":"$IMG_SIZE","active":$IMG_ACTIVE,"total":$IMG_TOTAL},
  "databases": $DB_JSON,
  "backups": $BACKUP_JSON,
  "history": $HIST
}
ENDJSON
mv "$TMPOUT" "$OUTPUT"
chmod 644 "$OUTPUT"

echo "[$(date)] Status updated → $OUTPUT"
