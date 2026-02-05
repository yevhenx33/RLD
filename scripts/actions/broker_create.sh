#!/bin/bash
# Create broker account for user
# Usage: broker_create.sh <USER_KEY> [ENV_VAR_NAME]
#
# Example: broker_create.sh 0xabc... USER_A_BROKER
# Returns: Broker address (also updates .env if ENV_VAR_NAME provided)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
VAR_NAME=$2

if [ -z "$USER_KEY" ]; then
    echo "Usage: broker_create.sh <USER_KEY> [ENV_VAR_NAME]" >&2
    exit 1
fi

SALT=$(cast keccak "broker-$(date +%s)-$RANDOM")

# Log to stderr so it doesn't get captured
echo -e "${YELLOW}[1] Creating broker...${NC}" >&2

BROKER_TX=$(cast send "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" --json)

# Extract broker address from BrokerCreated event
BROKER=$(echo "$BROKER_TX" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for log in data.get('logs', []):
    topics = log.get('topics', [])
    if topics and topics[0].lower() == '0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71':
        data_field = log.get('data', '')
        if data_field.startswith('0x') and len(data_field) >= 66:
            print('0x' + data_field[26:66])
            break
")

if [ -z "$BROKER" ]; then
    echo -e "${RED}✗ Failed to create broker${NC}" >&2
    exit 1
fi

# Update .env if var name provided
if [ -n "$VAR_NAME" ]; then
    "$SCRIPT_DIR/../utils/update_env.sh" "$VAR_NAME" "$BROKER"
fi

echo -e "${GREEN}✓ Broker: $BROKER${NC}" >&2

# Only output the broker address to stdout (for capture)
echo "$BROKER"
