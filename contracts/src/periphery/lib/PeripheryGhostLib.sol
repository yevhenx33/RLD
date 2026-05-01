// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {IGhostRouter} from "../../dex/interfaces/IGhostRouter.sol";

library PeripheryGhostLib {
    using PoolIdLibrary for PoolKey;
    using SafeTransferLib for ERC20;

    error UnexpectedHook();
    error PoolKeyMismatch();

    function ghostPoolId(PoolKey calldata poolKey) internal pure returns (bytes32) {
        PoolKey memory key = poolKey;
        key.hooks = IHooks(address(0));
        return PoolId.unwrap(key.toId());
    }

    function validatePoolKey(
        PoolKey calldata poolKey,
        address tokenA,
        address tokenB
    ) internal pure {
        if (address(poolKey.hooks) != address(0)) revert UnexpectedHook();

        address currency0 = Currency.unwrap(poolKey.currency0);
        address currency1 = Currency.unwrap(poolKey.currency1);
        bool valid = (currency0 == tokenA && currency1 == tokenB) ||
            (currency0 == tokenB && currency1 == tokenA);
        if (!valid) revert PoolKeyMismatch();
    }

    function zeroForOne(
        PoolKey calldata poolKey,
        address tokenIn
    ) internal pure returns (bool) {
        return Currency.unwrap(poolKey.currency0) == tokenIn;
    }

    function swapExactInput(
        address ghostRouter,
        PoolKey calldata poolKey,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) internal returns (uint256 amountOut) {
        validatePoolKey(poolKey, tokenIn, tokenOut);
        bool zfo = zeroForOne(poolKey, tokenIn);
        ERC20(tokenIn).safeApprove(ghostRouter, amountIn);
        amountOut = IGhostRouter(ghostRouter).swap(
            ghostPoolId(poolKey),
            zfo,
            amountIn,
            minAmountOut
        );
    }
}
