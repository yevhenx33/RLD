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

# Install Python deps for deploy scripts and DB registration
RUN pip3 install --no-cache-dir web3 eth-abi python-dotenv psycopg2-binary

WORKDIR /workspace

# Copy contracts (Forge project)
COPY contracts/ /workspace/contracts/

# Copy scripts (actions + scenarios + utils)
COPY scripts/ /workspace/scripts/

# Copy deploy_swap_router.py
COPY backend/tools/deploy_swap_router.py /workspace/backend/tools/deploy_swap_router.py

# Copy the new snapshot deploy orchestrator
COPY docker/deployer/deploy_protocol_snapshot.py /workspace/deploy_protocol_snapshot.py

# Copy the master deploy script + modular phases
COPY docker/deployer/deploy_all.sh /workspace/deploy_all.sh
COPY docker/deployer/phases/ /workspace/phases/
COPY docker/deployer/lib_setup.sh /workspace/lib_setup.sh
RUN chmod +x /workspace/deploy_all.sh /workspace/deploy_protocol_snapshot.py /workspace/phases/*.sh

# Pre-install forge dependencies
WORKDIR /workspace/contracts
RUN forge install --no-commit 2>/dev/null || true

WORKDIR /workspace

# Reset Foundry's ENTRYPOINT which is ["/bin/sh", "-c"]
ENTRYPOINT []
CMD ["python3", "/workspace/deploy_protocol_snapshot.py"]
