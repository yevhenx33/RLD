# TODO — Pre-Mainnet Blockers

## 1. Unused `deltaCollateral` Parameter

- [ ] Remove from internal functions (dead code from refactor)

## 2. Public `applyFunding()`

- [ ] Remove `applyFunding()` from RLDCore — marked `TODO: REMOVE BEFORE PRODUCTION`

## 3. Oracle Design, Tests & Integration

- [ ] Finalize oracle architecture (pricing sources, fallbacks)
- [ ] Integration testing with production feeds
- Business-level decision, not a code bug

## 4. Gas & Security Optimizations

- [ ] Storage packing audit (unnecessary SLOADs)
- [ ] Gas profiling on liquidation + solvency paths
- [ ] Formal verification of solvency invariant

## 5. Deployment

- [ ] Deploy to Sepolia testnet
- [ ] Deploy to Ethereum mainnet