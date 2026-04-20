#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# RLD Simulation Restart (Compatibility Wrapper)
# ═══════════════════════════════════════════════════════════════
# DEPRECATED:
#   Use docker/reth/restart-reth.sh directly for the canonical
#   Reth-only launch flow. This file remains for backward compat.
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RETH_RESTART="$SCRIPT_DIR/reth/restart-reth.sh"

if [ ! -x "$RETH_RESTART" ]; then
    echo "Error: $RETH_RESTART not found or not executable" >&2
    exit 1
fi

echo "⚠️  docker/restart.sh is deprecated. Redirecting to docker/reth/restart-reth.sh ..."
exec "$RETH_RESTART" "$@"
