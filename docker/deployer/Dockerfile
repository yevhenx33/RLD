# ═══════════════════════════════════════════════════════════════
# RLD Deployer — Foundry + Python
# Deploys protocol, market, users, swap router in one shot.
# Writes /config/deployment.json then exits.
# ═══════════════════════════════════════════════════════════════
FROM ghcr.io/foundry-rs/foundry:latest

# Install Python (for deploy_swap_router.py) and jq, curl
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv jq curl bash \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps for deploy_swap_router.py
RUN pip3 install --no-cache-dir web3 python-dotenv

WORKDIR /workspace

# Copy contracts (Forge project)
COPY contracts/ /workspace/contracts/

# Copy scripts (actions + scenarios + utils)
COPY scripts/ /workspace/scripts/

# Copy deploy_swap_router.py
COPY backend/tools/deploy_swap_router.py /workspace/backend/tools/deploy_swap_router.py

# Copy the master deploy script
COPY docker/deployer/deploy_all.sh /workspace/deploy_all.sh
RUN chmod +x /workspace/deploy_all.sh

# Pre-install forge dependencies
WORKDIR /workspace/contracts
RUN forge install --no-commit 2>/dev/null || true

WORKDIR /workspace

# Reset Foundry's ENTRYPOINT which is ["/bin/sh", "-c"]
ENTRYPOINT []
CMD ["bash", "/workspace/deploy_all.sh"]
