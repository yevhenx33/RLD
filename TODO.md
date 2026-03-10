# TODO — Pre-Mainnet Blockers

## 0. Min Position Size

- [ ] Set min position size for markets

## 1. Unused `deltaCollateral` Parameter

- [ ] Remove from internal liquidation functions (dead code from refactor)

## 2. Public `applyFunding()`

- [ ] Remove `applyFunding()` from RLDCore — marked `TODO: REMOVE BEFORE PRODUCTION`
- Allows gas griefing and unexpected NF changes

## 3. Oracle Design, Tests & Integration

- [ ] Finalize oracle architecture (pricing sources, fallbacks)
- [ ] Integration testing with production feeds
- Business-level decision, not a code bug

## 4. Cleanup

- [ ] Organize `script/` into `deploy/` vs `debug/` dirs
- [ ] Connect Bond NFT `tokenURI()` renderer
- [ ] Remove `.bak` test files
- [ ] Clean debug scripts from production artifact

## 5. Gas & Security Optimizations

- [ ] Storage packing audit (unnecessary SLOADs)
- [ ] Gas profiling on liquidation + solvency paths
- [ ] Review optimizer runs (200 may be suboptimal)
- [ ] Formal verification of solvency invariant

## 6. Deployment

- [ ] Deploy to Sepolia testnet
- [ ] Deploy to Ethereum mainnet