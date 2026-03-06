# Integration Guide

This guide is for developers building on top of RLD — aggregators, strategy vaults, portfolio managers, and other protocols.

## Reading Market State

### Market Configuration

```solidity
MarketConfig memory config = RLDCore.getMarketConfig(marketId);

// config.collateralToken     — ERC20 collateral address
// config.positionToken        — wRLP address
// config.indexOracle          — Index price oracle
// config.markOracle           — Mark price oracle
// config.maintenanceMargin    — Liquidation threshold (WAD)
// config.minCollateralRatio   — Opening threshold (WAD)
// config.closeFactor          — Max liquidation % (WAD)
// config.debtCap              — Max total debt (0 = unlimited)
```

### Current Prices

```javascript
// ethers.js
const indexPrice = await indexOracle.getIndexPrice(marketId);
const markPrice = await markOracle.getMarkPrice(poolId);
const spotPrice = await spotOracle.getSpotPrice(collateralToken);

// Index price = K × borrowRate (in WAD, 18 decimals)
// Mark price = TWAP from V4 pool (sqrtPriceX96 format)
```

### Position Data

```solidity
Position memory pos = RLDCore.getPosition(marketId, brokerAddress);

// pos.debtPrincipal    — Original debt amount
// pos.collateralAmount — Deposited collateral

uint256 nf = RLDCore.getNormalizationFactor(marketId);
uint256 trueDebt = pos.debtPrincipal * nf / 1e18; // Apply NF
```

## Programmatic Trading

### Creating a Broker

```solidity
address broker = PrimeBrokerFactory.createBroker();
// broker is now an ERC-721 NFT owned by msg.sender
// tokenId = uint256(uint160(broker))
```

### Depositing and Minting

All position modifications go through the `lock()` pattern:

```solidity
// Via BrokerRouter (recommended)
BrokerRouter.depositAndMint(
    broker,
    marketId,
    collateralAmount,
    debtAmount
);

// Via custom contract (advanced)
RLDCore.lock(
    abi.encodeCall(MyContract.lockCallback, (broker, marketId, amounts))
);
```

### Subscribing to Events

Key events to monitor:

```solidity
// Position changes
event PositionModified(bytes32 marketId, address broker, int256 collateralDelta, int256 debtDelta);
event FundingApplied(bytes32 marketId, uint256 newNF, int256 fundingRate);
event Liquidated(bytes32 marketId, address broker, address liquidator, uint256 repayAmount, uint256 seizeAmount);

// JTM orders
event SubmitOrder(PoolId poolId, bytes32 orderId, address owner, uint256 amountIn, uint256 expiration);
event CancelOrder(PoolId poolId, bytes32 orderId, uint256 refund);
event AuctionClear(PoolId poolId, address clearer, uint256 amount, uint256 discount);

// Bonds
event BondMinted(address broker, bytes32 marketId, uint256 amount, uint256 duration);
```

## Common Integration Patterns

### Strategy Vault

A vault contract that manages multiple positions:

1. Vault creates multiple PrimeBrokers (one per strategy)
2. Vault calls BrokerRouter for position management
3. Vault monitors health ratios and rebalances
4. Vault NFTs are held by the vault contract

### Aggregator

A router that finds the best execution across pools:

1. Query multiple RLD markets for rates
2. Compare spot vs JTM streaming execution
3. Route trades to the best venue
4. Use BrokerExecutor for atomic multi-step operations

### Liquidation Bot

An automated liquidator:

1. Index all active positions via events
2. Query `isSolvent()` periodically or via mempool monitoring
3. Calculate profitability based on health-based bonus
4. Execute via `RLDCore.liquidate()` with Flashbots
