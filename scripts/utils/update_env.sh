#!/bin/bash
# Update or append a key=value pair in .env
# Usage: update_env.sh <KEY> <VALUE>

ENV_FILE="/home/ubuntu/RLD/.env"
KEY=$1
VALUE=$2

if [ -z "$KEY" ] || [ -z "$VALUE" ]; then
    echo "Usage: update_env.sh <KEY> <VALUE>"
    exit 1
fi

# Create file if doesn't exist
touch "$ENV_FILE"

# Update existing or append
if grep -q "^${KEY}=" "$ENV_FILE"; then
    sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$ENV_FILE"
else
    echo "${KEY}=${VALUE}" >> "$ENV_FILE"
fi
