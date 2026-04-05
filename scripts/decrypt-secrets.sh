#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Decrypting secrets..."

if [ -f "$PROJECT_DIR/docker/.env.enc" ]; then
  sops --decrypt --input-type dotenv --output-type dotenv "$PROJECT_DIR/docker/.env.enc" > "$PROJECT_DIR/docker/.env"
  chmod 600 "$PROJECT_DIR/docker/.env"
  echo "  ✓ docker/.env"
fi

if [ -f "$PROJECT_DIR/data-pipeline/.env.enc" ]; then
  sops --decrypt --input-type dotenv --output-type dotenv "$PROJECT_DIR/data-pipeline/.env.enc" > "$PROJECT_DIR/data-pipeline/.env"
  chmod 600 "$PROJECT_DIR/data-pipeline/.env"
  echo "  ✓ data-pipeline/.env"
fi

echo "Done."
