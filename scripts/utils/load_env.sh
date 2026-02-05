#!/bin/bash
# Load centralized .env file
# Usage: source /home/ubuntu/RLD/scripts/utils/load_env.sh

set -a
source /home/ubuntu/RLD/.env 2>/dev/null || true
set +a
