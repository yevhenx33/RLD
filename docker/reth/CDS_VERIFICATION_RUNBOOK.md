# CDS Market Verification Runbook

Phase 9 verifies a deployed CDS market without advancing to runtime simulation.

## Read-Only Verification

Run after `deploy_cds_market_live.py` has written `markets.cds`:

```bash
python3 docker/reth/verify_cds_market_live.py --skip-indexer
```

Then, after the indexer is reset/seeded for multi-market config:

```bash
python3 docker/reth/verify_cds_market_live.py
```

The verifier checks:

- `RLDCore.isValidMarket(cdsMarketId) == true`
- CDS collateral is raw USDC
- `fundingModel == CDSDecayFundingModel`
- `settlementModule == CDSSettlementProxy`
- `decayRateWad > 0` and matches deployment config
- projected CDS NF is positive and non-increasing
- settlement proxy points to the same `RLDCore`
- settlement proxy requires 2-of-3 tracks
- V4/Ghost pool id and spot price match oracle-derived initialization
- `/config?market=cds` and `/api/market-info?market=cds` return the CDS market when indexer checks are enabled

## Stateful Checks

These are intentionally not performed by the read-only verifier:

- `RLDCore.applyFunding(cdsMarketId)`
- underwriter broker creation
- delayed withdrawal queue execution
- `CDSSettlementProxy.submitSettlementAttestation`
- `RLDCore.enterGlobalSettlement`

Run those only in a controlled staging sequence because they mutate the live simulation state.

## Runtime Actor Setup

After the CDS market is deployed and read-only verification passes, initialize CDS-specific
actors separately from the existing perp simulation:

```bash
python3 docker/reth/setup_cds_simulation.py --dry-run
python3 docker/reth/setup_cds_simulation.py
```

The script:

- uses `markets.cds`
- funds actors with raw USDC
- creates a CDS underwriter broker
- deposits `$100M` raw USDC by default
- mints bounded `wCDSUSDC`
- withdraws minted CDS tokens to the underwriter wallet for later LP/sale

It does not start or reconfigure `mm-daemon`, `chaos-trader`, or the existing perp setup.
