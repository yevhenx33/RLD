#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Anvil Snapshot Cleanup
# ═══════════════════════════════════════════════════════════════
# Removes old Anvil state snapshots from ~/.foundry/anvil/tmp/,
# keeping only the most recent one. Safe to run while Anvil is
# running — it only removes older, unused snapshots.
#
# Usage:
#   ./docker/scripts/cleanup-anvil-snapshots.sh          # interactive
#   ./docker/scripts/cleanup-anvil-snapshots.sh --force   # no confirmation
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

ANVIL_TMP="$HOME/.foundry/anvil/tmp"
FORCE=false

for arg in "$@"; do
    case "$arg" in
        --force|-f) FORCE=true ;;
    esac
done

if [ ! -d "$ANVIL_TMP" ]; then
    echo "No Anvil tmp directory found at $ANVIL_TMP"
    exit 0
fi

# Find all anvil-state-* directories, sorted newest first
mapfile -t SNAPSHOTS < <(ls -dt "$ANVIL_TMP"/anvil-state-* 2>/dev/null)

TOTAL=${#SNAPSHOTS[@]}

if [ "$TOTAL" -le 1 ]; then
    echo "✅ Nothing to clean — only ${TOTAL} snapshot(s) found."
    exit 0
fi

LATEST="${SNAPSHOTS[0]}"
TO_DELETE=("${SNAPSHOTS[@]:1}")
DELETE_COUNT=${#TO_DELETE[@]}

# Calculate space used by old snapshots
OLD_SIZE=$(du -shc "${TO_DELETE[@]}" 2>/dev/null | tail -1 | awk '{print $1}')

echo "═══════════════════════════════════════════════════════"
echo "  Anvil Snapshot Cleanup"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Total snapshots:  $TOTAL"
echo "  Keeping:          $(basename "$LATEST")"
echo "  Removing:         $DELETE_COUNT snapshot(s)"
echo "  Space to free:    ~$OLD_SIZE"
echo ""

if [ "$FORCE" = false ]; then
    echo "Snapshots to delete:"
    for s in "${TO_DELETE[@]}"; do
        SIZE=$(du -sh "$s" 2>/dev/null | awk '{print $1}')
        echo "  ❌ $(basename "$s")  ($SIZE)"
    done
    echo ""
    read -rp "Proceed? [y/N] " CONFIRM
    if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# Delete old snapshots
for s in "${TO_DELETE[@]}"; do
    NAME=$(basename "$s")
    rm -rf "$s"
    echo "  🗑️  Removed $NAME"
done

echo ""
echo "✅ Cleaned $DELETE_COUNT old snapshot(s), freed ~$OLD_SIZE"
echo "  Kept: $(basename "$LATEST")"
