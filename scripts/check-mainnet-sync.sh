#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Reth Mainnet Sync Monitor
# ═══════════════════════════════════════════════════════════════
# Usage: ./scripts/check-mainnet-sync.sh
# Checks both Lighthouse (CL) and Reth (EL) sync status.
# Run periodically or in a `watch` loop:
#   watch -n 30 ./scripts/check-mainnet-sync.sh

set -euo pipefail

echo "═══════════════════════════════════════════════"
echo "  Mainnet Node Sync Status — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════"

# --- Lighthouse (CL) ---
echo ""
echo "⛓️  LIGHTHOUSE (Consensus Layer)"
CL_SYNC=$(curl -sf http://127.0.0.1:5052/eth/v1/node/syncing 2>/dev/null || echo '{"data":{"is_syncing":"UNREACHABLE"}}')
CL_IS_SYNCING=$(echo "$CL_SYNC" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('is_syncing','?'))" 2>/dev/null || echo "?")
CL_HEAD=$(echo "$CL_SYNC" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('head_slot','?'))" 2>/dev/null || echo "?")
CL_DISTANCE=$(echo "$CL_SYNC" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('sync_distance','0'))" 2>/dev/null || echo "?")

if [ "$CL_IS_SYNCING" = "false" ] || [ "$CL_IS_SYNCING" = "False" ]; then
    echo "   Status:   ✅ SYNCED"
else
    echo "   Status:   🔄 SYNCING (distance: $CL_DISTANCE slots)"
fi
echo "   Head Slot: $CL_HEAD"
CL_PEERS=$(curl -sf http://127.0.0.1:5052/eth/v1/node/peer_count 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('connected','?'))" 2>/dev/null || echo "?")
echo "   Peers:    $CL_PEERS"

# --- Reth (EL) ---
echo ""
echo "🔧 RETH (Execution Layer)"
EL_SYNC=$(curl -sf -X POST http://127.0.0.1:8546 \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_syncing","params":[],"id":1}' 2>/dev/null || echo '{"result":false}')

EL_RESULT=$(echo "$EL_SYNC" | python3 -c "import json,sys; d=json.load(sys.stdin); print(type(d.get('result')).__name__)" 2>/dev/null || echo "?")

if [ "$EL_RESULT" = "bool" ]; then
    echo "   Status:   ✅ SYNCED (eth_syncing = false)"
    # Get latest block
    LATEST=$(curl -sf -X POST http://127.0.0.1:8546 \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' 2>/dev/null | python3 -c "import json,sys; print(int(json.load(sys.stdin)['result'],16))" 2>/dev/null || echo "?")
    echo "   Latest:   Block $LATEST"
else
    # Parse stages
    STAGES=$(echo "$EL_SYNC" | python3 -c "
import json, sys
d = json.load(sys.stdin).get('result', {})
stages = d.get('stages', [])
for s in stages:
    blk = int(s.get('block', '0x0'), 16) if s.get('block','0x0').startswith('0x') else int(s.get('block', 0))
    if blk > 0:
        print(f'     {s[\"name\"]:30s} block {blk:>12,}')
" 2>/dev/null || echo "     (parsing error)")
    echo "   Status:   🔄 SYNCING"
    if [ -n "$STAGES" ]; then
        echo "   Stages:"
        echo "$STAGES"
    fi
fi

# Get EL peer count
EL_PEERS=$(curl -sf -X POST http://127.0.0.1:8546 \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}' 2>/dev/null | python3 -c "import json,sys; print(int(json.load(sys.stdin)['result'],16))" 2>/dev/null || echo "?")
echo "   Peers:    $EL_PEERS"

# --- Disk ---
echo ""
echo "💾 DISK"
df -h / | tail -1 | awk '{print "   Used: " $3 " / " $2 " (" $5 " used, " $4 " free)"}'

echo ""
echo "═══════════════════════════════════════════════"
