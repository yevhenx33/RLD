#!/bin/bash
# install.sh — Install reth binary if not present
set -euo pipefail

if command -v reth &>/dev/null; then
    echo "reth already installed: $(reth --version 2>/dev/null | head -1)"
    exit 0
fi

echo "Installing reth..."

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

case "$ARCH" in
    x86_64)  ARCH="x86_64" ;;
    aarch64|arm64) ARCH="aarch64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

case "$OS" in
    linux)  PLATFORM="x86_64-unknown-linux-gnu"
            [ "$ARCH" = "aarch64" ] && PLATFORM="aarch64-unknown-linux-gnu" ;;
    darwin) PLATFORM="x86_64-apple-darwin"
            [ "$ARCH" = "aarch64" ] && PLATFORM="aarch64-apple-darwin" ;;
    *) echo "Unsupported OS: $OS"; exit 1 ;;
esac

# Get latest release
LATEST=$(curl -sL "https://api.github.com/repos/paradigmxyz/reth/releases/latest" | grep '"tag_name"' | cut -d'"' -f4)
if [ -z "$LATEST" ]; then
    echo "Failed to get latest reth release"
    echo "Install manually: https://paradigmxyz.github.io/reth/installation"
    exit 1
fi

URL="https://github.com/paradigmxyz/reth/releases/download/${LATEST}/reth-${LATEST}-${PLATFORM}.tar.gz"
echo "  Downloading reth ${LATEST} for ${PLATFORM}..."

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"

curl -sL "$URL" | tar xz -C "$INSTALL_DIR" reth 2>/dev/null

if [ -f "$INSTALL_DIR/reth" ]; then
    chmod +x "$INSTALL_DIR/reth"
    echo "  Installed to $INSTALL_DIR/reth"
    # Add to PATH hint
    if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
        echo "  Add to PATH: export PATH=\"$INSTALL_DIR:\$PATH\""
    fi
else
    echo "  Download failed. Install manually:"
    echo "    cargo install --locked reth"
    echo "    or: https://paradigmxyz.github.io/reth/installation"
    exit 1
fi
