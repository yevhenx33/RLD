# CDS Agent Guide

This guide is the operational handoff for agents working on the RLD CDS market.
Read it before changing contracts, deploy scripts, indexer code, frontend CDS
views, or trading bots.

## Current Mental Model

RLD now has two live market families over the same Aave USDC borrow-rate oracle:

| Market | Purpose | Collateral | Position token | Route |
|---|---|---|---|---|
| RLP | Rate-level perp for directional rate trading and fixed-rate products | `waUSDC` | `wRLP` | `/markets/perps/<rlp-market-id>` |
| CDS | Parametric protection market for rate-spike/default coverage | raw `USDC` | displayed as `wCDS` | `/markets/perps/cds` or `/markets/perps/<cds-market-id>` |

The CDS market is intentionally using the same terminal UX as the perp market:
users can long, short, mint, close, LP, and trade through the same perps terminal.
The difference is the market config and economics:

- CDS collateral is raw USDC.
- CDS uses `CDSDecayFundingModel`.
- CDS has a non-zero `CDSSettlementProxy`.
- CDS token value/coverage decays by `NF(t) = exp(-F * t)`.
- Constant coverage requires replenishing `wCDS` over time.

## Core CDS Economics

The CDS paper models the token as an amortizing perpetual option.

Definitions:

- `P_index = 100 * r_t`
- `P_max = 100 * r_max`
- `NF(t) = exp(-F * t)`
- `F = -ln(1 - targetUtilization)`

Current default parameters:

- `targetUtilization = 0.90`
- `F = 2302585092994045684` WAD
- `r_max = 75%`
- `P_max = 75`
- reserve factor assumption in economic simulations: `10%`

The invariant we verify off-chain is:

```text
Y_CDS = F * (r_t / r_max) >= r_supply = U_t * r_t * (1 - R)
```

Validity bound:

```text
r_max <= F / (1 - R)
```

For the current parameters:

- configured `r_max = 0.75`
- max valid `r_max ~= 2.5584`
- invariant holds with buffer.

## Contracts

### Funding

File:

- `contracts/src/rld/modules/funding/CDSDecayFundingModel.sol`

Behavior:

- Reads `decayRateWad` from `RLDCore.getMarketConfig(marketId)`.
- Rejects `decayRateWad == 0` after time passes.
- Rejects zero current NF.
- Rejects `expWad` underflow to zero.
- Rejects rounded `newNormalizationFactor == 0`.
- Returns `fundingRate = F` for logging/indexer semantics.

Important: standard RLP markets use `StandardFundingModel`; CDS uses
`CDSDecayFundingModel`. Do not wire CDS with `fundingModel = address(0)`,
because the factory will use the default standard funding model.

### Settlement

File:

- `contracts/src/rld/modules/settlement/CDSSettlementProxy.sol`

The proxy is the market's `settlementModule` in `RLDCore`.

It supports:

- owner emergency settlement
- operator allowlisting
- operator attestation submission
- 2-of-3 track mask validation
- attestation replay protection
- forwarding to `RLDCore.enterGlobalSettlement`
- invalidating broker withdrawal queues through `RLDCore`

Current tracks:

- `TRACK_UTILIZATION_FREEZE`
- `TRACK_COLLATERAL_COLLAPSE`
- `TRACK_BAD_DEBT_ACCRUAL`

Future Symbiotic integration should plug into `submitSettlementAttestation`.
The proxy is not yet the final ZK/Symbiotic settlement engine; it is the
production-facing integration point that lets `RLDCore` enter global settlement.

### Broker Withdrawal Queue

File:

- `contracts/src/rld/broker/PrimeBroker.sol`

Important CDS-specific rule:

- Debt-bearing CDS brokers must queue collateral withdrawals.
- They can still withdraw position tokens, so underwriters can mint `wCDS` and
  sell/LP those tokens.

This was hardened because the first implementation blocked all token
withdrawals for debt-bearing CDS brokers, which prevented underwriters from
withdrawing minted `wCDS`.

Test:

- `contracts/test/rld/CDSBrokerWithdrawalQueue.t.sol`

## Deployment Scripts

### Deploy CDS Market On Live Reth

File:

- `docker/reth/deploy_cds_market_live.py`

Run after a fresh Reth simulation is up:

```bash
python3 docker/reth/deploy_cds_market_live.py
```

It:

- reuses existing `RLDCore`, `RLDMarketFactory`, `GhostRouter`, `TwapEngine`
- deploys `CDSDecayFundingModel`
- deploys `CDSSettlementProxy`
- creates a raw-USDC CDS market
- verifies `RLDCore` market registration
- verifies `GhostRouter` spot price against oracle-derived init price
- writes `markets.cds` into `docker/deployment.json`

Do not use the Anvil genesis deployment path for CDS after Reth is already
running. This script is specifically for adding CDS on top of the live node.

### Verify CDS Market

File:

- `docker/reth/verify_cds_market_live.py`

Read-only on-chain verification:

```bash
python3 docker/reth/verify_cds_market_live.py --skip-indexer
```

Full verification after indexer config sync:

```bash
python3 docker/reth/verify_cds_market_live.py
```

Checks:

- CDS market exists in `RLDCore`
- collateral is raw USDC
- `fundingModel` is `CDSDecayFundingModel`
- `settlementModule` is `CDSSettlementProxy`
- `decayRateWad` is non-zero and matches config
- funding projection is positive and non-increasing
- settlement proxy points at the same core
- proxy has 2-of-3 track policy
- pool id and Ghost spot match oracle-derived price
- GraphQL/indexer can resolve `market=cds`

### CDS Runtime Setup

File:

- `docker/reth/setup_cds_simulation.py`

Default economic setup:

```bash
python3 docker/reth/setup_cds_simulation.py --dry-run
python3 docker/reth/setup_cds_simulation.py
```

Requested current default flow:

- fund underwriter with `60M USDC`
- fund buyer with `10M USDC`
- create CDS underwriter broker
- post `50M USDC` as broker collateral
- mint `$500k` worth of `wCDS`
- withdraw minted `wCDS` to underwriter account
- calculate matching USDC for LP
- add CDS V4 liquidity

Useful resume mode:

```bash
python3 docker/reth/setup_cds_simulation.py --lp-only
```

Use `--lp-only` if the script already created the broker/minted/withdrew tokens
but failed before LP minting.

Known current LP setup artifact:

- `docker/reth/cds-simulation-setup-report.json`

## Restart / Launch Sequence

Canonical full sequence after a clean simulation restart:

```bash
# 1. Restart Reth simulation with users and bots
bash docker/reth/restart-reth.sh --fresh --with-users --with-bots

# 2. Deploy CDS on top of the live Reth node
python3 docker/reth/deploy_cds_market_live.py

# 3. Sync indexer config without deleting history
curl -sf -X POST \
  -H "X-Admin-Token: test_token" \
  http://localhost:8080/admin/sync-config

# 4. Verify CDS market
python3 docker/reth/verify_cds_market_live.py

# 5. Seed CDS LP
python3 docker/reth/setup_cds_simulation.py --dry-run
python3 docker/reth/setup_cds_simulation.py
```

If the verifier fails because `markets.cds` exists on disk but the fresh core
does not recognize it, the simulation was restarted and the CDS config is stale.
Run `deploy_cds_market_live.py` again.

## Indexer Architecture

The simulation indexer remains Postgres-backed because it is an operational
state indexer:

- brokers
- balances
- active LPs
- TWAMM orders
- snapshots
- current pool state
- frontend operational UX

The ClickHouse indexer remains analytics-oriented:

- long historical timeseries
- protocol TVL
- APY history
- flows
- research/backtests

Do not replace the simulation indexer with ClickHouse yet. Instead, the
simulation indexer has adopted ClickHouse-style additive configuration:

- `POST /admin/sync-config`
- `POST /admin/rewind-market`
- `source_status`
- per-market cursors
- no destructive reset required to add markets

### Config Sync

Non-destructive normal path:

```bash
curl -sf -X POST \
  -H "X-Admin-Token: test_token" \
  http://localhost:8080/admin/sync-config
```

This reads `docker/deployment.json`, upserts `markets.perp` and `markets.cds`,
seeds missing cursors, and preserves history.

Only use reset when you truly want to wipe:

```bash
curl -sf -X POST \
  -H "X-Admin-Token: test_token" \
  http://localhost:8080/admin/reset
```

### Per-Market Rewind

For replay/backfill without truncation:

```bash
curl -sf -X POST \
  -H "X-Admin-Token: test_token" \
  "http://localhost:8080/admin/rewind-market?market_id=<market-id>&block=<block>"
```

Use this when handler logic changes and a market needs event replay.

## Frontend

### Repository Page

Route:

- `http://localhost:5173/markets/perps`

This page intentionally shows both:

- `wRLP / USD`
- `wCDS / USD`

It uses one GraphQL query with aliases:

```graphql
query PerpsRepositoryMarkets {
  perpInfo: marketInfo(market: "perp")
  perpSnapshot: snapshot(market: "perp")
  cdsInfo: marketInfo(market: "cds")
  cdsSnapshot: snapshot(market: "cds")
}
```

No REST shortcuts should be used for this.

### Terminal Page

RLP:

```text
/markets/perps/<rlp-market-id>
```

CDS:

```text
/markets/perps/cds
/markets/perps/<cds-market-id>
```

`SimulationTerminal` selects market data from the route param:

- `cds` or CDS market id -> `marketKey = cds / market_id`
- RLP market id -> RLP data

CDS terminal uses the same long/short/trade UI as RLP but shows CDS-native
labels:

- collateral: `USDC`
- position token display: `wCDS`
- header: `wCDS / USD`
- product: `CDS MARKET`

## GraphQL API

Use GraphQL for frontend production code.

Market-specific fields:

```graphql
query($market: String) {
  snapshot(market: $market)
  marketInfo(market: $market)
  liquidityDistribution(market: $market)
}
```

Examples:

```graphql
snapshot(market: "perp")
snapshot(market: "cds")
snapshot(market: "0xc3fec6e9...")
```

`/config?market=cds` and `/api/market-info?market=cds` exist for compatibility
and operational scripts, but do not use REST shortcuts in frontend code.

## Bots

### Existing Bots

`mm-daemon` and `chaos-trader` are still RLP/perp bots. They use default
`/config`, which resolves to the perp market.

### Cross-Market Arb Bot

File:

- `backend/tools/cross_market_arb.py`

Compose service:

- `arb-bot`

It:

- reads both markets via GraphQL
- computes RLP basis and CDS basis
- if RLP rich: sell RLP, buy CDS
- if CDS rich: sell CDS, buy RLP
- uses `GhostRouterSwapExecutor`
- preflights both legs before live execution
- writes heartbeat/status to `/tmp/cross_market_arb_status.json`
- has Docker healthcheck

Default env:

```text
ARB_DRY_RUN=true
ARB_THRESHOLD_BPS=75
ARB_TRADE_USD=1000
ARB_MIN_USDC=10000
ARB_INTERVAL_SECONDS=15
```

Live mode example:

```bash
ARB_DRY_RUN=false \
ARB_THRESHOLD_BPS=10 \
ARB_MIN_USDC=1000000 \
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env up -d --build arb-bot
```

Check logs:

```bash
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs --tail=120 arb-bot
```

Healthy live execution logs look like:

```text
Executing pair: sell perp ... -> quoted ..., buy cds ... -> quoted ...
```

## Operational Checks

### Stack

```bash
bash docker/scripts/stack.sh ps
```

Expected simulation services:

- `reth`
- `postgres`
- `indexer`
- `faucet`
- `mm-daemon`
- `chaos-trader`
- optionally `arb-bot`

### CDS Verification

```bash
python3 docker/reth/verify_cds_market_live.py
```

### GraphQL Smoke

```bash
python3 - <<'PY'
import json, urllib.request

query = '''
query($market: String) {
  snapshot(market: $market)
  marketInfo(market: $market)
}
'''

for market in [None, "cds"]:
    req = urllib.request.Request(
        "http://localhost:8080/graphql",
        data=json.dumps({"query": query, "variables": {"market": market}}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())["data"]
    print(market, data["marketInfo"]["marketId"], data["marketInfo"]["collateral"]["symbol"])
PY
```

Expected:

- default -> RLP/waUSDC
- `cds` -> CDS/USDC

### Indexed Swaps

```bash
docker exec reth-postgres-1 psql -U rld -d rld_indexer -c "
SELECT market_id, resolution, SUM(volume_usd) AS volume, SUM(swap_count) AS swaps
FROM candles
GROUP BY market_id, resolution
ORDER BY market_id, resolution;
"
```

After LP and arb activity, CDS should have candle rows and non-zero swaps.

## Common Failure Modes

### `markets.cds` exists but `RLDCore.isValidMarket(cds) == false`

Cause: Reth restarted, but `docker/deployment.json` still has stale CDS config.

Fix:

```bash
python3 docker/reth/deploy_cds_market_live.py
curl -sf -X POST -H "X-Admin-Token: test_token" http://localhost:8080/admin/sync-config
python3 docker/reth/verify_cds_market_live.py
```

### Frontend CDS terminal shows RLP mark

Likely route mismatch.

Valid CDS terminal routes:

```text
/markets/perps/cds
/markets/perps/<cds-market-id>
```

`SimulationTerminal` should pass the route param as `marketKey` into
`useSimulation`.

### LP page shows zero mark or zero volume

Zero mark means DB pool state is missing. GraphQL now falls back to
`deployment.json.pool_spot_price_wad` for mark/index hydration.

Zero volume/fees means no swaps have occurred since the current replay window,
or swap routing did not process V4 `Swap` events. Confirm `Swap` is listed in
`TOPIC1_POOL_ID_TO_MARKET_EVENTS` in `backend/indexers/indexer.py`.

### Need to replay a market without deleting history

Use:

```bash
curl -sf -X POST \
  -H "X-Admin-Token: test_token" \
  "http://localhost:8080/admin/rewind-market?market_id=<market-id>&block=<deploy-or-earlier-block>"
```

### Arb bot is healthy but not trading

Check:

- `ARB_DRY_RUN`
- threshold vs observed spread
- USDC inventory
- wRLP inventory
- wCDS inventory
- CDS LP TVL

Command:

```bash
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs --tail=120 arb-bot
```

## Files Future Agents Usually Need

Contracts:

- `contracts/src/rld/modules/funding/CDSDecayFundingModel.sol`
- `contracts/src/rld/modules/settlement/CDSSettlementProxy.sol`
- `contracts/src/rld/broker/PrimeBroker.sol`

Deployment and verification:

- `docker/reth/deploy_cds_market_live.py`
- `docker/reth/verify_cds_market_live.py`
- `docker/reth/setup_cds_simulation.py`
- `docker/reth/CDS_VERIFICATION_RUNBOOK.md`

Indexer:

- `backend/indexers/bootstrap.py`
- `backend/indexers/api/graphql.py`
- `backend/indexers/indexer.py`
- `backend/indexers/handlers/pool.py`
- `backend/indexers/handlers/lp.py`
- `backend/indexers/state.py`

Frontend:

- `frontend/src/hooks/useSimulation.js`
- `frontend/src/components/trading/PerpsDirectory.jsx`
- `frontend/src/components/trading/SimulationTerminal.jsx`
- `frontend/src/components/cds/CdsDirectory.jsx`
- `frontend/src/components/cds/Cds.jsx`

Bots:

- `backend/tools/cross_market_arb.py`
- `backend/tools/chaos_daemon.py`
- `backend/services/combined_daemon.py`
- `docker/reth/docker-compose.reth.yml`

Economic verification:

- `backend/rates/cds_economic_verification.py`
- `backend/rates/cds_underwriter_verification.py`
- `backend/rates/artifacts/cds_economic_verification.md`
- `backend/rates/artifacts/cds_underwriter_verification.md`

## Immediate Agent Checklist

When joining an active CDS session:

1. Check stack health.

```bash
bash docker/scripts/stack.sh ps
```

2. Verify deployment config.

```bash
python3 docker/reth/verify_cds_market_live.py
```

3. Verify frontend GraphQL paths.

```bash
python3 - <<'PY'
import json, urllib.request
q='query($m:String){ marketInfo(market:$m) snapshot(market:$m) }'
for m in [None,'cds']:
  req=urllib.request.Request('http://localhost:8080/graphql', data=json.dumps({'query':q,'variables':{'m':m}}).encode(), headers={'Content-Type':'application/json'}, method='POST')
  print(m, json.loads(urllib.request.urlopen(req).read().decode())['data']['marketInfo']['collateral']['symbol'])
PY
```

4. Verify CDS liquidity.

```bash
docker exec reth-postgres-1 psql -U rld -d rld_indexer -c "
SELECT market_id, snapshot IS NOT NULL AS has_snapshot, total_broker_wausdc, total_broker_wrlp
FROM markets ORDER BY deploy_block;
"
```

5. Check arb bot.

```bash
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env ps arb-bot
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs --tail=80 arb-bot
```

6. If adding/redeploying CDS on a fresh Reth, never use reset just for config.

```bash
python3 docker/reth/deploy_cds_market_live.py
curl -sf -X POST -H "X-Admin-Token: test_token" http://localhost:8080/admin/sync-config
python3 docker/reth/verify_cds_market_live.py
```

That is the safe baseline.
