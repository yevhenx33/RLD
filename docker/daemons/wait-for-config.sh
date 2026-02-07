#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Wait for /config/deployment.json and export all values as env vars.
# Then exec the actual daemon command.
# ═══════════════════════════════════════════════════════════════

set -e

CONFIG_FILE=${CONFIG_FILE:-"/config/deployment.json"}

echo "⏳ Waiting for deployment config at $CONFIG_FILE..."

for i in $(seq 1 120); do
    if [ -f "$CONFIG_FILE" ]; then
        echo "✅ Config found!"
        break
    fi
    sleep 2
done

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Timed out waiting for $CONFIG_FILE"
    exit 1
fi

# Export all config values as env vars (only if not already set)
echo "📋 Loading config..."

export_if_unset() {
    local KEY=$1 VALUE=$2
    if [ -z "${!KEY}" ]; then
        export "$KEY=$VALUE"
        echo "  $KEY=$VALUE"
    fi
}

export_if_unset "RLD_CORE"        "$(jq -r '.rld_core'        "$CONFIG_FILE")"
export_if_unset "TWAMM_HOOK"      "$(jq -r '.twamm_hook'      "$CONFIG_FILE")"
export_if_unset "MARKET_ID"       "$(jq -r '.market_id'       "$CONFIG_FILE")"
export_if_unset "WAUSDC"          "$(jq -r '.wausdc'          "$CONFIG_FILE")"
export_if_unset "POSITION_TOKEN"  "$(jq -r '.position_token'  "$CONFIG_FILE")"
export_if_unset "BROKER_FACTORY"  "$(jq -r '.broker_factory'  "$CONFIG_FILE")"
export_if_unset "SWAP_ROUTER"     "$(jq -r '.swap_router'     "$CONFIG_FILE")"
export_if_unset "POOL_MANAGER"    "$(jq -r '.pool_manager'    "$CONFIG_FILE")"
export_if_unset "TOKEN0"          "$(jq -r '.token0'          "$CONFIG_FILE")"
export_if_unset "TOKEN1"          "$(jq -r '.token1'          "$CONFIG_FILE")"
export_if_unset "MOCK_ORACLE"     "$(jq -r '.mock_oracle'     "$CONFIG_FILE")"
export_if_unset "MOCK_ORACLE_ADDR" "$(jq -r '.mock_oracle'    "$CONFIG_FILE")"

echo ""
echo "🚀 Starting: $@"
exec "$@"
