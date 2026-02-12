## Foundry

**Foundry is a blazing fast, portable and modular toolkit for Ethereum application development written in Rust.**

Foundry consists of:

- **Forge**: Ethereum testing framework (like Truffle, Hardhat and DappTools).
- **Cast**: Swiss army knife for interacting with EVM smart contracts, sending transactions and getting chain data.
- **Anvil**: Local Ethereum node, akin to Ganache, Hardhat Network.
- **Chisel**: Fast, utilitarian, and verbose solidity REPL.

## Documentation

https://book.getfoundry.sh/

## Usage

### Build

```shell
$ forge build
```

### Test

```shell
$ forge test
```

### Format

```shell
$ forge fmt
```

### Gas Snapshots

```shell
$ forge snapshot
```

### Anvil

```shell
$ anvil
```

### Deploy

```shell
$ forge script script/Counter.s.sol:CounterScript --rpc-url <your_rpc_url> --private-key <your_private_key>
```

### Cast

```shell
$ cast <subcommand>
```

### Help

```shell
$ forge --help
$ anvil --help
$ cast --help
```

---

## Close Short (BrokerRouter)

Added `closeShort` to `BrokerRouter.sol` — allows partial or full repayment of a short position's wRLP debt by spending waUSDC collateral.

### Function Signature

```solidity
function closeShort(
    address broker,
    uint256 collateralToSpend,
    PoolKey calldata poolKey
) external onlyBrokerAuthorized(broker) nonReentrant returns (uint256 debtRepaid)
```

### Execution Flow

1. **Withdraw collateral** — `pb.withdrawCollateral(router, collateralToSpend)` pulls waUSDC from broker
2. **Swap waUSDC → wRLP** — exact-input swap via V4 PoolManager
3. **Transfer wRLP to broker** — router sends bought wRLP back to broker
4. **Repay debt** — `pb.modifyPosition(marketId, 0, -debtRepaid)` burns wRLP via RLDCore
5. **Return leftover** — any residual collateral returned to broker

### Event

```solidity
event ShortClosed(address indexed broker, uint256 debtRepaid, uint256 collateralSpent);
```

### Deployment

Deployed via `script/DeployBrokerRouter.s.sol`. After deployment:

- Call `setDepositRoute(waUSDC, route)` to configure deposit wrapping path
- Call `broker.setOperator(routerAddress, true)` to authorize the router

### Known Limitation: TWAP Oracle Staleness

The `closeShort` flow triggers `_applyFunding()` in RLDCore, which calls `observe(secondsAgo=3600)` on the TWAMM oracle. On Anvil, `block.timestamp` tracks wall-clock time, so oracle observations go stale if no swaps happen for >24 minutes. A keep-alive script doing periodic tiny swaps is needed to keep the oracle fresh.
