# Frontend/Indexer/Core Operational Hardening Blueprint

## Indexer-First Frontend Rule

The simulation indexer is the frontend's canonical runtime source of truth. The frontend must load market IDs, pool IDs, token addresses, router/factory addresses, feature flags, chain metadata, and readiness state from `GET /api/runtime-manifest`.

Contracts remain canonical for execution semantics only: route previews, wallet-specific reads, transaction submission, and post-transaction verification. The frontend must not read `docker/deployment.json` or rely on production contract-address environment fallbacks.

## Runtime Manifest Contract

Preferred endpoint:

```http
GET /api/runtime-manifest
```

Manifest v1 fields:

- `schemaVersion`: manifest schema version, currently `1`.
- `deploymentId`: stable short hash of core runtime addresses and market IDs.
- `chainId`: expected chain ID, currently `31337`.
- `rpcUrl`: public RPC proxy URL for browser and wallet clients.
- `faucetUrl`: public faucet API URL.
- `indexerBlock`: highest block indexed by the simulation indexer.
- `chainBlock`: latest chain block observed over RPC.
- `readiness`: `{ ready, status, reasons, indexerLagBlocks, maxIndexerLagBlocks }`.
- `contracts`: shared core/periphery addresses.
- `markets`: keyed by market type, currently `perp` and `cds`.

Existing `/config` and `/api/market-info` remain compatibility endpoints. New frontend integration should use the runtime manifest first.

## Frontend Execution Gates

Execution controls must be disabled or degraded to read-only mode when any of these checks fail:

- Runtime manifest is missing, invalid, or not schema v1.
- Manifest readiness is not `ready`.
- Wallet chain ID does not equal manifest `chainId`.
- Selected market is missing from `markets`.
- Required execution contract address is missing.
- Required contract code is absent at the selected address.
- Route preview is missing, stale, loading, or invalid.
- Indexer lag exceeds `readiness.maxIndexerLagBlocks`.

Executable min/max bounds must come from contract route previews for the selected market. Raw pool or quoter data can be displayed as informational context, but it cannot be the final source for slippage bounds.

## Wallet Requirements

Wallet connection must:

1. Request accounts.
2. Add or switch to chain `31337`.
3. Re-read wallet chain ID.
4. Read latest RPC block.
5. Re-read `/api/runtime-manifest`.
6. Allow execution only when the manifest, chain, market, contract-code, and route-preview gates pass.

MetaMask network values:

- RPC URL: public HTTPS RPC proxy URL.
- Chain ID: `31337`.
- Currency symbol: `ETH`.
- Explorer: omit unless a compatible explorer is deployed.

## Faucet Requirements

Faucet UI must be idempotent:

- Check token balances before requesting funds.
- Skip faucet requests when the wallet is already above the configured funded threshold.
- Disable repeat clicks while a faucet request is pending.
- Poll balances after a successful faucet response.
- Treat rate limits as non-fatal when refreshed balances show the wallet is already funded.

## Demo Cutover Command

The single intentional replacement flow is:

```bash
python3 docker/reth/simctl.py demo-cutover --replace-chain
```

The command must refuse to run without `--replace-chain`. A successful cutover performs:

1. Fresh Reth deployment.
2. Ghost oracle priming with the established 60 second period.
3. User seeding.
4. Bot startup.
5. CDS market deployment.
6. Indexer `sync-config`.
7. CDS verifier.
8. CDS demo liquidity seed.
9. `verify-runtime`.
10. Indexer smoke test.

Do not use this command during a live client demo unless intentionally replacing the persistent demo chain.

## Daily Demo Health Checklist

Run before demo windows:

```bash
cd /home/ubuntu/RLD
export PATH="$HOME/.foundry/bin:$PATH"
cast block-number --rpc-url http://localhost:8545
python3 docker/reth/simctl.py verify-runtime
python3 docker/reth/simctl.py smoke
curl -sf http://localhost:8080/api/runtime-manifest | jq .
curl -sf http://localhost:8080/readyz
curl -sf http://localhost:8088/health
```

Confirm:

- Chain is advancing.
- Manifest readiness is `ready`.
- `perp` and `cds` markets are both present.
- Indexer lag is inside the configured threshold.
- Frontend loads without CORS errors.
- MetaMask can switch/add chain `31337`.
- Faucet funds only wallets below threshold.

## Prohibited Live-Demo Actions

Do not run these during live demo windows unless intentionally replacing the chain:

- `python3 docker/reth/simctl.py restart --fresh`
- `python3 docker/reth/simctl.py demo-cutover --replace-chain`
- `/admin/reset`
- `python3 docker/reth/deploy_cds_market_live.py`
- Raw public exposure of `8545`

Use `/admin/sync-config` for configuration drift after deployments or config updates. Use targeted bot restarts for bot failures.
