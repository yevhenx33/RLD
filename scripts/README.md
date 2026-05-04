# RLD Scripts

This folder is for operator-adjacent helpers and local protocol simulations.
Production services do not import or execute these scripts at runtime.

## Production-Adjacent Helpers

### `decrypt-secrets.sh`

Decrypts encrypted environment files into local `.env` files:

```bash
./scripts/decrypt-secrets.sh
```

### `check-mainnet-sync.sh`

Checks Reth and Lighthouse sync status on the host:

```bash
./scripts/check-mainnet-sync.sh
watch -n 30 ./scripts/check-mainnet-sync.sh
```

## Local Simulation Harness

These scripts are developer tools for Anvil/fork simulation and manual protocol
flows. They are not part of the Docker production runtime.

```bash
./scripts/lifecycle_test.sh
./scripts/stress_test.sh
./scripts/chaos_test.sh
```

The lower-level action/scenario helpers are kept because they compose the local
simulation flows:

- `scripts/actions/*`
- `scripts/scenarios/*`
- `scripts/utils/*`

## Common Simulation Flows

```bash
# Deploy local wrapped market state
./scripts/deploy_wrapped_market.sh

# Trade and LP helpers
./scripts/go_long.sh
./scripts/go_short.sh
./scripts/test_twamm_order.sh
./scripts/mint_and_lp_wrapped.sh
./scripts/mint_and_lp_executor.sh
```

Scripts read from `.env` at the repository root via `scripts/utils/load_env.sh`.
Required values vary by flow, but usually include:

```bash
WAUSDC=0x...
POSITION_TOKEN=0x...
TWAMM_HOOK=0x...
MARKET_ID=0x...
BROKER_FACTORY=0x...
PRIVATE_KEY=0x...
ETH_RPC_URL=http://127.0.0.1:8545
```

## Removed From This Folder

Generated research artifacts, one-off Aave/IRM analysis scripts, the old
Postgres indexer prototype, and one-shot event scraper scripts were removed
because they are not production runtime surfaces. Production indexing is owned
by `backend/indexers` and `backend/analytics`.
