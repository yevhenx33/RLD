#!/bin/bash
# warmup.sh — Touch contracts to ensure Anvil loads their state
#
# Anvil forks lazily — it only fetches contract code/storage when accessed.
# Before dumping state, we need to "warm" any contracts that the user might
# need, so they end up in the dump.
#
# Usage: warmup.sh <ANVIL_RPC> <DATA_DIR>

set -euo pipefail

RPC="${1:-http://localhost:8545}"
DATA_DIR="${2:-.forge-persist}"

# Touch well-known DeFi contracts (mainnet addresses)
KNOWN_CONTRACTS=(
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # USDC
    "0xdAC17F958D2ee523a2206206994597C13D831ec7"  # USDT
    "0x6B175474E89094C44Da98b954EedeAC495271d0F"  # DAI
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"  # WBTC
    "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"  # wstETH
    "0x43506849D7C04F9138D1A2050bbF3A0c054402dd"  # USDC impl
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"  # Aave V3 Pool
    "0x000000000004444c5dc75cB358380D2e3dE08A90"  # Uniswap V4 PM
    "0x000000000022D473030F116dDEE9F6B43aC78BA3"  # Permit2
)

warmup_count=0
for addr in "${KNOWN_CONTRACTS[@]}"; do
    cast code "$addr" --rpc-url "$RPC" > /dev/null 2>&1 || true
    cast balance "$addr" --rpc-url "$RPC" > /dev/null 2>&1 || true
    warmup_count=$((warmup_count + 1))
done

# Also warm any contracts from deployment artifacts if they exist
for json_file in "$DATA_DIR"/*.json broadcast/**/run-latest.json; do
    [ -f "$json_file" ] || continue
    for addr in $(jq -r '.. | strings | select(test("^0x[0-9a-fA-F]{40}$"))' "$json_file" 2>/dev/null | sort -u | head -50); do
        cast code "$addr" --rpc-url "$RPC" > /dev/null 2>&1 || true
        warmup_count=$((warmup_count + 1))
    done
done

echo "  $warmup_count contracts warmed"
