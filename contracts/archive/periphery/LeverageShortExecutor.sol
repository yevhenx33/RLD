// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    ReentrancyGuard
} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {PrimeBroker} from "../rld/broker/PrimeBroker.sol";
import {IERC20} from "../shared/interfaces/IERC20.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {CurrencySettler} from "v4-core/test/utils/CurrencySettler.sol";

/// @title  LeverageShortExecutor — Single-Swap Leveraged Short via Ephemeral Operator
/// @author RLD Protocol
/// @notice Atomically opens a leveraged short position: deposit collateral,
///         mint max debt as wRLP, swap wRLP → waUSDC via V4, deposit
///         proceeds as additional collateral, revoke operator.
///
/// @dev ## Atomic Flow
///
///     ```
///     Owner signs(executor, broker, nonce, executor)
///                    │
///     ┌──────────────▼────────────────────────────────────────┐
///     │  1. setOperatorWithSignature(self, true, sig)          │
///     │  2. modifyPosition(+collateral, +debt)                │
///     │  3. withdrawPositionToken(self, debt)                 │
///     │  4. approve(poolManager, debt)                        │
///     │  5. V4 swap: wRLP → waUSDC  (exact input)            │
///     │  6a. transfer(waUSDC → broker)                        │
///     │  6b. sweep(residual wRLP → broker)                    │
///     │  7. modifyPosition(+proceeds, 0)                     │
///     │  8. setOperator(self, false)   ◄── ALWAYS             │
///     └───────────────────────────────────────────────────────┘
///     ```
///
/// ## Security Model
///
///     1. **Ephemeral Operator** — Same pattern as BrokerExecutor.
///        Operator is set and revoked within the same transaction.
///     2. **Exact Input Swap** — Uses negative `amountSpecified` to
///        ensure the full debt amount is swapped (exact input in V4).
///     3. **Residual Sweep** — Any leftover wRLP after the swap is
///        swept back to the broker to prevent token leakage.
///     4. **Solvency Check** — The final `modifyPosition()` call
///        triggers a solvency check via `RLDCore.lockAndCallback()`,
///        rejecting the transaction if the position is underwater.
///
/// ## Access Control
///
///     | Function                   | Guard          | Who Can Call |
///     |----------------------------|----------------|-------------|
///     | `executeLeverageShort()`    | `nonReentrant` | Anyone*     |
///     | `unlockCallback()`         | `msg.sender`   | PoolManager |
///     | `calculateOptimalDebt()`   | `pure`         | Anyone      |
///     | `getMessageHash()`         | `view`         | Anyone      |
///     | `getEthSignedMessageHash()`| `view`         | Anyone      |
///
///     *Requires valid owner signature to set operator on the broker.
///
/// ## Test Coverage (Phase 6 — 8 tests)
///
///     - End-to-end leveraged short with proceeds deposit
///     - Broker stays solvent after leverage
///     - Operator always revoked post-execution
///     - No residual tokens left in executor
///     - V4 unlock callback restricted to PoolManager
///     - `calculateOptimalDebt()` math at 0%, 40%, 50%, 80% LTV
///     - Excessive leverage (5× on 10k) correctly rejected
///
/// ## Bugs Found & Fixed During Penetration Testing
///
///     1. **`amountSpecified` sign bug** — Used positive value
///        (exact output in V4) instead of negative (exact input).
///        This left ~8 wRLP residual in the executor.
///        Fix: changed to `-int256(targetDebtAmount)`.
///     2. **No wRLP sweep** — After the swap, any residual wRLP
///        was stranded in the executor contract forever.
///        Fix: added sweep of remaining wRLP back to broker.
///
/// ## Known Limitations
///
///     - Relies on caller-supplied `collateralToken` and
///       `positionToken` addresses (less secure than BrokerRouter
///       which reads these from the broker).
///     - `calculateOptimalDebt()` does not account for swap slippage,
///       so actual leverage may be slightly below target.
///     - Only supports single-swap leverage (no multi-hop routes).
contract LeverageShortExecutor is ReentrancyGuard {
    using CurrencySettler for Currency;

    IPoolManager public immutable poolManager;

    /// @notice Sentinel value meaning "use entire balance"
    uint256 public constant USE_ALL_BALANCE = type(uint256).max;

    struct SwapCallback {
        address sender;
        PoolKey key;
        SwapParams params;
    }

    constructor(address _poolManager) {
        poolManager = IPoolManager(_poolManager);
    }

    /// @notice Execute leveraged short in one atomic transaction
    /// @param broker The PrimeBroker address
    /// @param marketId The market to short
    /// @param collateralToken waUSDC address
    /// @param positionToken wRLP address
    /// @param initialCollateral Amount of initial collateral already in broker
    /// @param targetDebtAmount Total wRLP to mint (pre-calculated for target leverage)
    /// @param poolKey V4 pool key for swap
    /// @param ownerSignature EIP-191 signature from broker owner
    function executeLeverageShort(
        address broker,
        bytes32 marketId,
        address collateralToken,
        address positionToken,
        uint256 initialCollateral,
        uint256 targetDebtAmount,
        PoolKey calldata poolKey,
        bytes calldata ownerSignature
    ) external nonReentrant {
        PrimeBroker pb = PrimeBroker(payable(broker));

        // 1. Set self as operator
        uint256 nonce = pb.operatorNonces(address(this));
        pb.setOperatorWithSignature(
            address(this),
            true,
            ownerSignature,
            nonce,
            bytes32(0)
        );

        // 2. Deposit initial collateral and mint all target debt
        pb.modifyPosition(
            marketId,
            int256(initialCollateral),
            int256(targetDebtAmount)
        );

        // 3. Withdraw wRLP to this executor
        pb.withdrawToken(pb.positionToken(), address(this), targetDebtAmount);

        // 4. Approve pool manager for swap
        IERC20(positionToken).approve(address(poolManager), targetDebtAmount);

        // 5. Single swap: wRLP → waUSDC
        bool zeroForOne = positionToken < collateralToken;

        SwapParams memory swapParams = SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -int256(targetDebtAmount), // negative = exact input in V4
            sqrtPriceLimitX96: zeroForOne
                ? 4295128740 // MIN_SQRT_PRICE + 1
                : 1461446703485210103287273052203988822378723970341 // MAX_SQRT_PRICE - 1
        });

        BalanceDelta delta = abi.decode(
            poolManager.unlock(
                abi.encode(
                    SwapCallback({
                        sender: address(this),
                        key: poolKey,
                        params: swapParams
                    })
                )
            ),
            (BalanceDelta)
        );

        // Calculate proceeds (the token we received)
        uint256 proceeds = zeroForOne
            ? uint256(int256(delta.amount1()))
            : uint256(int256(delta.amount0()));

        // 6. Transfer ALL proceeds back to broker (USE_ALL_BALANCE pattern)
        uint256 collateralBalance = IERC20(collateralToken).balanceOf(
            address(this)
        );
        IERC20(collateralToken).transfer(broker, collateralBalance);

        // 6b. Sweep any residual wRLP back to broker
        uint256 posRemainder = IERC20(positionToken).balanceOf(address(this));
        if (posRemainder > 0) {
            IERC20(positionToken).transfer(broker, posRemainder);
        }

        // 7. Deposit proceeds as additional collateral (no new debt)
        // Using actual balance since we know it now
        pb.modifyPosition(marketId, int256(collateralBalance), int256(0));

        // 8. Revoke operator
        pb.setOperator(address(this), false);
    }

    /// @notice Callback for V4 swap
    function unlockCallback(
        bytes calldata rawData
    ) external returns (bytes memory) {
        require(msg.sender == address(poolManager), "Not PM");

        SwapCallback memory data = abi.decode(rawData, (SwapCallback));

        BalanceDelta delta = poolManager.swap(
            data.key,
            data.params,
            new bytes(0)
        );

        // Settle tokens
        if (data.params.zeroForOne) {
            if (delta.amount0() < 0) {
                data.key.currency0.settle(
                    poolManager,
                    data.sender,
                    uint256(-int256(delta.amount0())),
                    false
                );
            }
            if (delta.amount1() > 0) {
                data.key.currency1.take(
                    poolManager,
                    data.sender,
                    uint256(int256(delta.amount1())),
                    false
                );
            }
        } else {
            if (delta.amount1() < 0) {
                data.key.currency1.settle(
                    poolManager,
                    data.sender,
                    uint256(-int256(delta.amount1())),
                    false
                );
            }
            if (delta.amount0() > 0) {
                data.key.currency0.take(
                    poolManager,
                    data.sender,
                    uint256(int256(delta.amount0())),
                    false
                );
            }
        }

        return abi.encode(delta);
    }

    /// @notice Calculate optimal debt for target leverage
    /// @param collateralAmount Initial collateral
    /// @param targetLTV Target loan-to-value (e.g., 40 = 40%)
    /// @param wRLPPriceE6 wRLP price in collateral terms (6 decimals)
    /// @return debtAmount Amount of wRLP to mint
    function calculateOptimalDebt(
        uint256 collateralAmount,
        uint256 targetLTV,
        uint256 wRLPPriceE6
    ) external pure returns (uint256 debtAmount) {
        // For iterative leverage: final_collateral = initial / (1 - LTV)
        // target_debt_value = final_collateral * LTV = initial * LTV / (1 - LTV)
        // debt_amount = target_debt_value / wRLP_price

        uint256 targetDebtValue = (collateralAmount * targetLTV) /
            (100 - targetLTV);
        debtAmount = (targetDebtValue * 1e6) / wRLPPriceE6;
    }

    /// @notice Generate message hash for signature
    function getMessageHash(
        address broker,
        uint256 nonce
    ) external view returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    address(this),
                    true,
                    broker,
                    nonce,
                    address(this),
                    bytes32(0),
                    block.chainid
                )
            );
    }

    function getEthSignedMessageHash(
        address broker,
        uint256 nonce
    ) external view returns (bytes32) {
        bytes32 messageHash = keccak256(
            abi.encode(
                address(this),
                true,
                broker,
                nonce,
                address(this),
                bytes32(0),
                block.chainid
            )
        );
        return
            keccak256(
                abi.encodePacked(
                    "\x19Ethereum Signed Message:\n32",
                    messageHash
                )
            );
    }
}
