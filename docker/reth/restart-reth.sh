#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RLD_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

exec python3 "$RLD_ROOT/docker/reth/simctl.py" restart "$@"
