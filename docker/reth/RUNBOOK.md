# Reth Simulation Runbook

`simctl.py` is the phase-based controller for the local Reth simulation stack.
It replaces the old all-in-one `restart-reth.sh` flow while keeping the wrapper
command for compatibility.

## Fresh Restart

```bash
python3 docker/reth/simctl.py restart --fresh --with-users --with-bots
```

Equivalent wrapper:

```bash
./docker/reth/restart-reth.sh --fresh --with-users --with-bots
```

Fast restart from the latest saved genesis:

```bash
python3 docker/reth/simctl.py restart --from-snapshot --with-users --with-bots
```

Reuse the current `genesis.json` and `deployment-snapshot.json`:

```bash
python3 docker/reth/simctl.py restart --skip-genesis --with-users --with-bots
```

## Resume By Failed Phase

The controller writes phase state to `docker/reth/.sim/state.json`. Check the
current view with:

```bash
python3 docker/reth/simctl.py status --json
```

Common resume commands:

```bash
# Genesis already exists, but Reth/indexer did not start.
python3 docker/reth/simctl.py start --skip-genesis

# Reth and indexer are running, but /config or TVL seeding failed.
python3 docker/reth/simctl.py seed-indexer

# Protocol is running, but LP/MM/CHAOS provisioning failed.
python3 docker/reth/simctl.py seed-users

# Users are funded, but bots or faucet failed.
python3 docker/reth/simctl.py start-bots
```

Bot failures are intentionally non-destructive: they mark runtime as degraded,
but do not wipe Reth, Postgres, genesis, or deployment artifacts.

## Verification Modes

Core verification is for pre-bot deployment checks:

```bash
python3 docker/reth/simctl.py verify-core
```

It validates contract code, pool ID derivation, initial oracle-to-spot wiring,
and indexer `/config` plus `/api/market-info`.

Runtime verification is for post-bot checks:

```bash
python3 docker/reth/simctl.py verify-runtime
```

It verifies services are running, the indexer cursor is on the local Reth chain,
the indexer has runtime rows/events when available, and pool state has non-zero
liquidity, balances, or price state. It does not require current spot price to
equal the initial deployment oracle price because bots can legitimately move the
pool after startup.

Simulation indexer smoke checks:

```bash
python3 docker/reth/simctl.py smoke
```

The smoke command checks `/healthz`, `/readyz`, `/api/status`, `/api/latest`,
per-market lag, route anomalies, and perp swap/candle aggregates when events are
present.

## Logs

```bash
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs -f
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs -f reth
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs -f indexer
docker compose -f docker/reth/docker-compose.reth.yml --env-file docker/.env logs -f mm-daemon chaos-trader faucet
```

Temporary Anvil genesis-generation logs are kept at `/tmp/anvil.log` when
genesis generation fails.

## Common Failures

- `Temporary internal error`, `failed to get account`, `HTTP 500`, or `code 19`
  during `forge create`: rerun `generate-genesis` or `restart --fresh`; transient
  deploy commands are retried automatically.
- Stale APY during genesis deployment: `simctl` checks
  `/api/v1/oracle/usdc-borrow-apy` with `DEPLOY_RATE_MAX_AGE_SECONDS` (default
  `7200`) because the deploy oracle is hourly. Runtime bots still use
  `MAX_RATE_AGE_SECONDS`.
- `seed-indexer` fails: keep Reth running, inspect indexer logs, then rerun
  `python3 docker/reth/simctl.py seed-indexer`.
- Runtime verification reports `indexer cursor is ahead`: the deployment
  snapshot still has fork-era block numbers. Regenerate with current `simctl`, or
  patch `deployment.json`/`deployment-snapshot.json` market entries to block `0`
  and rerun `python3 docker/reth/simctl.py seed-indexer`.
- `seed-users` fails: keep protocol services running, fix funding or nonce
  issue, then rerun `python3 docker/reth/simctl.py seed-users`.
- `start-bots` fails: runtime is degraded only. Inspect bot logs, rebuild if
  needed, then rerun `python3 docker/reth/simctl.py start-bots`.
- `verify-core` passes but `verify-runtime` reports spot drift: spot drift is
  expected after bots trade; runtime mode skips strict initial spot equality.
