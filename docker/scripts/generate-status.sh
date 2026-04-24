#!/bin/bash
# generate-status.sh — Collects comprehensive system metrics → status.json
# Cron: * * * * * sudo /home/ubuntu/RLD/docker/scripts/generate-status.sh

set -euo pipefail

OUTPUT="/home/ubuntu/RLD/docker/dashboard/status.json"
HISTORY="/home/ubuntu/RLD/docker/dashboard/history.json"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ENV_FILE="/home/ubuntu/RLD/docker/.env"
DEPLOYMENT_JSON="/home/ubuntu/RLD/docker/deployment.json"
BACKUP_SCRIPT="/home/ubuntu/RLD/docker/scripts/backup-databases.sh"
STATUS_SCRIPT="/home/ubuntu/RLD/docker/scripts/generate-status.sh"
RESTORE_SCRIPT="/home/ubuntu/RLD/docker/scripts/validate-backup-restore.sh"

read_env_value() {
  local key="$1"
  local default="$2"
  if [ ! -f "$ENV_FILE" ]; then
    echo "$default"
    return 0
  fi
  local value
  value=$(awk -F= -v k="$key" '$1 == k {print substr($0, index($0, "=") + 1)}' "$ENV_FILE" | tail -1 | tr -d '"' | tr -d "'")
  echo "${value:-$default}"
}

probe_http_time() {
  local url="$1"
  local result
  if result=$(curl -sf -o /dev/null -w "%{time_total}" -m 3 "$url" 2>/dev/null); then
    echo "$result"
  else
    echo "-1"
  fi
}

seconds_to_ms() {
  local seconds="$1"
  if [ "$seconds" = "-1" ]; then
    echo "-1"
    return 0
  fi
  python3 -c "print(int(float('$seconds')*1000))" 2>/dev/null || echo "-1"
}

service_container() {
  local service="$1"
  local running_only="${2:-true}"
  local scope="ps"
  if [ "$running_only" != "true" ]; then
    scope="ps -a"
  fi
  docker $scope --filter "label=com.docker.compose.service=${service}" --format '{{.Names}}' 2>/dev/null | head -1
}

cron_has_entry() {
  local needle="$1"
  if crontab -l 2>/dev/null | grep -qF "$needle"; then
    return 0
  fi
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    if sudo -n crontab -l 2>/dev/null | grep -qF "$needle"; then
      return 0
    fi
  fi
  return 1
}

INDEXER_PORT=$(read_env_value "INDEXER_PORT" "8080")
RATES_API_PORT=$(read_env_value "RATES_API_PORT" "")
if [ -z "$RATES_API_PORT" ]; then
  RATES_API_PORT=$(read_env_value "ENVIO_API_PORT" "")
fi
if [ -z "$RATES_API_PORT" ]; then
  RATES_API_PORT=$(read_env_value "ENVIO_PORT" "5000")
fi
BOT_PORT="8083"

# Fallback index price from deployment config (genesis-style deploys can have
# oracle state without replayable RateUpdated events in block_states).
deployment_index_price=$(python3 - "$DEPLOYMENT_JSON" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    raw = data.get("oracle_index_price_wad")
    if raw in (None, "", "0", 0):
        print("null")
    else:
        print(round(int(raw) / 1e18, 6))
except Exception:
    print("null")
PY
)

# ── System ──
DISK_TOTAL=$(df -BG / | awk 'NR==2{print $2}' | tr -d 'G')
DISK_USED=$(df -BG / | awk 'NR==2{print $3}' | tr -d 'G')
DISK_FREE=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
DISK_PCT=$(df / | awk 'NR==2{print $5}' | tr -d '%')
DATA_DISK_TOTAL=$(df -BG /mnt/data 2>/dev/null | awk 'NR==2{print $2}' | tr -d 'G' || echo 0)
DATA_DISK_USED=$(df -BG /mnt/data 2>/dev/null | awk 'NR==2{print $3}' | tr -d 'G' || echo 0)
DATA_DISK_FREE=$(df -BG /mnt/data 2>/dev/null | awk 'NR==2{print $4}' | tr -d 'G' || echo 0)
DATA_DISK_PCT=$(df /mnt/data 2>/dev/null | awk 'NR==2{print $5}' | tr -d '%' || echo 0)
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
rates_rt=$(probe_http_time "http://localhost:${RATES_API_PORT}/healthz")
indexer_rt=$(probe_http_time "http://localhost:${INDEXER_PORT}/healthz")
bot_rt=$(probe_http_time "http://localhost:${BOT_PORT}/")
nginx_rt=$(probe_http_time "https://rld.fi/")

rates_ok=$([ "$rates_rt" != "-1" ] && echo "true" || echo "false")
indexer_ok=$([ "$indexer_rt" != "-1" ] && echo "true" || echo "false")
bot_ok=$([ "$bot_rt" != "-1" ] && echo "true" || echo "false")
nginx_ok=$([ "$nginx_rt" != "-1" ] && echo "true" || echo "false")
rates_ms=$(seconds_to_ms "$rates_rt")
indexer_ms=$(seconds_to_ms "$indexer_rt")
bot_ms=$(seconds_to_ms "$bot_rt")
nginx_ms=$(seconds_to_ms "$nginx_rt")

rates_block=$(curl -sf -m 3 "http://localhost:${RATES_API_PORT}/graphql" \
  -H "Content-Type: application/json" \
  --data '{"query":"{ latestRates { timestamp } }"}' 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('latestRates',{}).get('timestamp',''))" 2>/dev/null || echo "")

ENVIO_HEALTH=$(curl -sf -m 2 "http://localhost:${RATES_API_PORT}/healthz" 2>/dev/null || echo '{}')

ENVIO_READY_TMP=$(mktemp)
ENVIO_READY_HTTP=$(curl -s -m 3 -o "$ENVIO_READY_TMP" -w "%{http_code}" "http://localhost:${RATES_API_PORT}/readyz" 2>/dev/null || echo "000")
eval "$(python3 - "$ENVIO_READY_HTTP" "$ENVIO_READY_TMP" <<'PY'
import json
import pathlib
import shlex
import sys

code_raw = sys.argv[1]
path = pathlib.Path(sys.argv[2])
body = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
try:
    payload = json.loads(body) if body.strip() else {}
except Exception:
    payload = {"raw": body.strip()[:500]}

code = int(code_raw) if str(code_raw).isdigit() else 0
ready = bool(code == 200 and payload.get("status") in ("ready", "ok"))
reason = str(payload.get("reason", ""))
failing = payload.get("failingProtocols") if isinstance(payload.get("failingProtocols"), list) else []
proc_lag = payload.get("processingLag") if isinstance(payload.get("processingLag"), dict) else {}
max_lag = -1
for value in proc_lag.values():
    try:
        max_lag = max(max_lag, int(value))
    except Exception:
        pass

print(f"ENVIO_READY={str(ready).lower()}")
print(f"ENVIO_READY_HTTP={code}")
print(f"ENVIO_READY_REASON={shlex.quote(reason)}")
print(f"ENVIO_READY_FAILING={shlex.quote(','.join(str(x) for x in failing))}")
print(f"ENVIO_READY_MAX_LAG={max_lag}")
print("ENVIO_READY_PAYLOAD=" + shlex.quote(json.dumps(payload, separators=(',', ':'))))
PY
)"
rm -f "$ENVIO_READY_TMP"

# Indexer API status (authoritative market metrics for dashboard)
INDEXER_API_STATUS=$(python3 - "$INDEXER_PORT" <<'PY'
import json
import sys
import urllib.request

port = sys.argv[1]
url = f"http://localhost:{port}/api/status"
try:
    with urllib.request.urlopen(url, timeout=2) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, dict):
        print(json.dumps(payload))
    else:
        print("{}")
except Exception:
    print("{}")
PY
)

# ── Market architecture (indexer API) ──
MARKET_INFO_JSON=$(python3 - "$INDEXER_PORT" <<'PY'
import json
import sys
import urllib.request

port = sys.argv[1]
url = f"http://localhost:{port}/api/market-info"
out = {}
try:
    with urllib.request.urlopen(url, timeout=2) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, dict) and payload.get("status") != "error":
        hook = (payload.get("twammHook") or payload.get("twamm_hook") or "").lower()
        out = {
            "market_id": payload.get("marketId") or payload.get("market_id") or "",
            "pool_id": payload.get("poolId") or payload.get("pool_id") or "",
            "pool_fee": payload.get("poolFee") or payload.get("pool_fee"),
            "tick_spacing": payload.get("tickSpacing") or payload.get("tick_spacing"),
            "mock_oracle": payload.get("mockOracle") or payload.get("mock_oracle") or "",
            "ghost_router": payload.get("ghostRouter") or payload.get("ghost_router") or "",
            "twap_engine": payload.get("twapEngine") or payload.get("twap_engine") or "",
            "twap_engine_lens": payload.get("twapEngineLens") or payload.get("twap_engine_lens") or "",
            "twamm_hook": hook,
            "pool_manager": payload.get("poolManager") or payload.get("pool_manager") or "",
            "hookless_pool": hook in ("", "0x", "0x0", "0x0000000000000000000000000000000000000000"),
        }
except Exception:
    out = {}

print(json.dumps(out))
PY
)

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
if [ "$(id -u)" -eq 0 ]; then
  if nginx -t >/dev/null 2>&1; then nginx_conf_ok="true"; else nginx_conf_ok="false"; fi
else
  if sudo -n nginx -t >/dev/null 2>&1; then nginx_conf_ok="true"; else nginx_conf_ok="false"; fi
fi

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
RESTORE_JSON=$(cat /home/ubuntu/RLD/backups/last_restore_check.json 2>/dev/null || echo '{"status":"never","timestamp":"never"}')
BACKUP_CRON_OK=false
STATUS_CRON_OK=false
RESTORE_CRON_OK=false
if cron_has_entry "$BACKUP_SCRIPT"; then BACKUP_CRON_OK=true; fi
if cron_has_entry "$STATUS_SCRIPT"; then STATUS_CRON_OK=true; fi
if cron_has_entry "$RESTORE_SCRIPT"; then RESTORE_CRON_OK=true; fi

# ── Database Integrity ──
DB_JSON=$(python3 -c "
import json
health = json.loads('''$ENVIO_HEALTH''') if '''$ENVIO_HEALTH'''.strip() else {}
readyz = json.loads('''$ENVIO_READY_PAYLOAD''') if '''$ENVIO_READY_PAYLOAD'''.strip() else {}
health['ready'] = '''$ENVIO_READY'''.strip().lower() == 'true'
health['readyHttp'] = int('''$ENVIO_READY_HTTP''' or 0)
health['readyReason'] = '''$ENVIO_READY_REASON'''
if isinstance(readyz, dict) and readyz:
    health['readyz'] = readyz
print(json.dumps({'envio_indexer': health}))
" 2>/dev/null || echo '{"envio_indexer":{"status":"unknown"}}')

# --- Pool state from rld_indexer postgres ---
PG_CONTAINER=$(service_container "postgres" false)
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
idx = json.loads('''$INDEXER_API_STATUS''') if '''$INDEXER_API_STATUS'''.strip() else {}
if idx.get('status') == 'ok':
    if idx.get('last_indexed_block') is not None:
        pool['last_indexed_block'] = idx.get('last_indexed_block')
    if idx.get('total_events') is not None:
        pool['total_events'] = idx.get('total_events')
    if idx.get('total_block_states') is not None:
        pool['block_states_rows'] = idx.get('total_block_states')
    if idx.get('mark_price') is not None:
        pool['mark_price'] = idx.get('mark_price')
    if idx.get('index_price') is not None:
        pool['index_price'] = idx.get('index_price')
    pool['price_source'] = 'indexer_api'

fallback_raw = '''$deployment_index_price'''.strip()
fallback = None
if fallback_raw and fallback_raw not in ('null', 'None'):
    try:
        fallback = float(fallback_raw)
    except ValueError:
        fallback = None
if pool.get('index_price') is None and fallback is not None:
    pool['index_price'] = fallback
    pool['index_price_source'] = 'deployment_json'
elif pool.get('index_price') is not None:
    pool['index_price_source'] = 'indexer_api' if pool.get('price_source') == 'indexer_api' else 'block_states'
rates['pool_state'] = pool
print(json.dumps(rates))
" 2>/dev/null) || DB_JSON='{}'

# ── Stack map (production-facing topology health) ──
STACKS_JSON=$(python3 -c "
import json

def to_bool(value):
    return str(value).strip().lower() == 'true'

def to_int(value, default=None):
    try:
        if value in (None, '', 'None'):
            return default
        return int(value)
    except Exception:
        return default

def group_state(containers, needles):
    matched = [c for c in containers if any(n in (c.get('name') or '').lower() for n in needles)]
    running = [c for c in matched if (c.get('status') or '').lower() in ('running', 'healthy')]
    healthy = [c for c in matched if (c.get('status') or '').lower() == 'healthy']
    return {
        'matched': len(matched),
        'running': len(running),
        'healthy': len(healthy),
        'names': [c.get('name') for c in matched][:8],
    }

containers = json.loads('''$containers_json''') if '''$containers_json'''.strip() else []
db = json.loads('''$DB_JSON''') if '''$DB_JSON'''.strip() else {}
envio = db.get('envio_indexer', {}) if isinstance(db.get('envio_indexer'), dict) else {}
pool = db.get('pool_state', {}) if isinstance(db.get('pool_state'), dict) else {}

legacy = group_state(containers, ['rates-indexer'])
frontend_container = group_state(containers, ['frontend'])
mm_container = group_state(containers, ['mm-daemon'])
chaos_container = group_state(containers, ['chaos-trader'])
faucet_container = group_state(containers, ['faucet'])

processing_lag = envio.get('processingLag', {}) if isinstance(envio.get('processingLag'), dict) else {}
lag_threshold = to_int((envio.get('readyz') or {}).get('maxLagBlocks'), 250000) if isinstance(envio.get('readyz'), dict) else 250000
failing_protocols = []
if isinstance(envio.get('readyz'), dict) and isinstance(envio['readyz'].get('failingProtocols'), list):
    failing_protocols = envio['readyz']['failingProtocols']

protocol_components = {
    'graphql_api': to_bool('''$rates_ok'''),
    'clickhouse': str(envio.get('clickhouse', '')).lower() == 'ok',
    'readiness': to_bool('''$ENVIO_READY'''),
}
if all(protocol_components.values()):
    protocol_status = 'healthy'
elif protocol_components['graphql_api'] and protocol_components['clickhouse']:
    protocol_status = 'degraded'
else:
    protocol_status = 'critical'

chain_block = to_int('''$anvil_block''')
indexer_block = to_int(pool.get('last_indexed_block'))
block_gap = (chain_block - indexer_block) if (chain_block is not None and indexer_block is not None) else None
simulation_components = {
    'sim_indexer_api': to_bool('''$indexer_ok'''),
    'postgres_state': bool(pool.get('healthy')),
    'reth_rpc': to_bool('''$anvil_ok'''),
}
if all(simulation_components.values()) and (block_gap is None or block_gap <= 200):
    simulation_status = 'healthy'
elif all(simulation_components.values()):
    simulation_status = 'degraded'
else:
    simulation_status = 'critical'

execution_components = {
    'monitor_bot_api': to_bool('''$bot_ok'''),
    'mm_daemon': mm_container['running'] > 0,
    'chaos_trader': chaos_container['running'] > 0,
    'faucet': faucet_container['running'] > 0,
}
if all(execution_components.values()):
    execution_status = 'healthy'
elif execution_components['monitor_bot_api'] and (execution_components['mm_daemon'] or execution_components['chaos_trader']):
    execution_status = 'degraded'
else:
    execution_status = 'critical'

frontend_components = {
    'edge_nginx': to_bool('''$nginx_ok'''),
    'frontend_container': frontend_container['running'] > 0,
}
frontend_status = 'healthy' if all(frontend_components.values()) else 'degraded'

legacy_running = legacy['running'] > 0
legacy_status = 'deprecated-running' if legacy_running else 'removed'

gates = {
    'protocol_rates_ready': protocol_status == 'healthy',
    'simulation_ready': simulation_status == 'healthy',
    'execution_ready': execution_status != 'critical',
    'frontend_ready': frontend_status == 'healthy',
    'legacy_removed': not legacy_running,
}
gates['production_ready'] = all(gates.values())

stacks = {
    'protocol_rates': {
        'label': 'Protocol Rates',
        'status': protocol_status,
        'components': protocol_components,
        'latestTimestamp': '''$rates_block''',
        'processingLag': processing_lag,
        'lagThreshold': lag_threshold,
        'failingProtocols': failing_protocols,
        'readyReason': '''$ENVIO_READY_REASON''',
    },
    'simulation': {
        'label': 'Simulation',
        'status': simulation_status,
        'components': simulation_components,
        'chainBlock': chain_block,
        'indexerBlock': indexer_block,
        'blockGap': block_gap,
    },
    'execution': {
        'label': 'Execution/Bots',
        'status': execution_status,
        'components': execution_components,
        'containers': {
            'mmDaemon': mm_container,
            'chaosTrader': chaos_container,
            'faucet': faucet_container,
        },
    },
    'frontend_edge': {
        'label': 'Frontend/Edge',
        'status': frontend_status,
        'components': frontend_components,
        'containers': frontend_container,
    },
    'legacy_rates': {
        'label': 'Legacy Rates Indexer',
        'status': legacy_status,
        'running': legacy_running,
        'containers': legacy,
    },
    'gates': gates,
}

print(json.dumps(stacks))
" 2>/dev/null || echo '{}')

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

# ── Node Metrics (Reth Mainnet / Lighthouse) ──
NODE_METRICS_JSON=$(python3 /home/ubuntu/RLD/docker/scripts/fetch_node_metrics.py 2>/dev/null) || NODE_METRICS_JSON='{}'

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
    "data_disk": {"total_gb":${DATA_DISK_TOTAL:-0},"used_gb":${DATA_DISK_USED:-0},"free_gb":${DATA_DISK_FREE:-0},"percent":${DATA_DISK_PCT:-0}},
    "memory": {"total_mb":$MEM_TOTAL,"used_mb":$MEM_USED,"available_mb":$MEM_AVAIL},
    "swap": {"total_mb":$SWAP_TOTAL,"used_mb":$SWAP_USED},
    "connections": {"established":$ESTAB,"listening":$LISTEN},
    "errors_today": $ERR_COUNT
  },
  "containers": $containers_json,
  "services": {
    "nginx": {"healthy":$nginx_conf_ok,"response_ms":$nginx_ms},
    "envio_indexer": {
      "healthy":$rates_ok,
      "response_ms":$rates_ms,
      "last_block":"$rates_block",
      "ready":$ENVIO_READY,
      "ready_http":$ENVIO_READY_HTTP,
      "ready_reason":"$ENVIO_READY_REASON",
      "ready_max_lag":$ENVIO_READY_MAX_LAG,
      "failing_protocols":"$ENVIO_READY_FAILING"
    },
    "indexer": {"healthy":$indexer_ok,"response_ms":$indexer_ms},
    "monitor_bot": {"healthy":$bot_ok,"response_ms":$bot_ms},
    "reth": {"healthy":$anvil_ok,"block":"$anvil_block","mem_mb":${reth_mem_mb:-0},"uptime":"$reth_uptime","db_mb":${reth_db_mb:-0},"chain_id":"${reth_chain_id}","gas_gwei":"${reth_gas_price}","block_ts":${reth_block_ts:-0},"txpool_pending":${reth_txpool_pending:-0},"txpool_queued":${reth_txpool_queued:-0}},
    "anvil": {"healthy":$anvil_ok,"block":"$anvil_block","mem_mb":${reth_mem_mb:-0},"uptime":"$reth_uptime","db_mb":${reth_db_mb:-0},"chain_id":"${reth_chain_id}","gas_gwei":"${reth_gas_price}","block_ts":${reth_block_ts:-0},"txpool_pending":${reth_txpool_pending:-0},"txpool_queued":${reth_txpool_queued:-0}}
  },
  "ssl": {"expiry":"$SSL_EXPIRY","days_remaining":${SSL_DAYS:-0}},
  "git": {"commit":"$GIT_COMMIT","message":"$GIT_MSG","time":"$GIT_TIME","author":"$GIT_AUTHOR"},
  "docker": {"dangling_images":$DANGLING,"images_size":"$IMG_SIZE","active":$IMG_ACTIVE,"total":$IMG_TOTAL},
  "databases": $DB_JSON,
  "market": $MARKET_INFO_JSON,
  "nodes": $NODE_METRICS_JSON,
  "stacks": $STACKS_JSON,
  "automation": {"status_job_scheduled":$STATUS_CRON_OK,"backup_job_scheduled":$BACKUP_CRON_OK,"restore_job_scheduled":$RESTORE_CRON_OK},
  "backups": $BACKUP_JSON,
  "restore_checks": $RESTORE_JSON,
  "history": $HIST
}
ENDJSON
mv "$TMPOUT" "$OUTPUT"
chmod 644 "$OUTPUT"

echo "[$(date)] Status updated → $OUTPUT"
