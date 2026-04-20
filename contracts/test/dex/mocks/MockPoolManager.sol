// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import {BalanceDelta, toBalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

interface IUnlockCallbackLike {
    function unlockCallback(bytes calldata data) external returns (bytes memory);
}

interface IERC20Like {
    function transfer(address to, uint256 amount) external returns (bool);
}

contract MockPoolManager {
    bytes32 internal slot0Word;

    uint256 internal swapOutputNumerator = 1;
    uint256 internal swapOutputDenominator = 1;

    function setSqrtPriceX96(uint160 sqrtPriceX96) external {
        slot0Word = bytes32(uint256(sqrtPriceX96));
    }

    function initialize(PoolKey memory, uint160 sqrtPriceX96) external returns (int24 tick) {
        slot0Word = bytes32(uint256(sqrtPriceX96));
        return 0;
    }

    function setSwapOutputRatio(uint256 numerator, uint256 denominator) external {
        require(numerator > 0 && denominator > 0, "bad ratio");
        swapOutputNumerator = numerator;
        swapOutputDenominator = denominator;
    }

    function extsload(bytes32) external view returns (bytes32) {
        return slot0Word;
    }

    function unlock(bytes calldata data) external returns (bytes memory result) {
        return IUnlockCallbackLike(msg.sender).unlockCallback(data);
    }

    function swap(PoolKey memory, SwapParams memory params, bytes calldata) external view returns (BalanceDelta swapDelta) {
        uint256 amountIn = uint256(-params.amountSpecified);
        uint256 amountOut = (amountIn * swapOutputNumerator) / swapOutputDenominator;

        if (params.zeroForOne) {
            // Router owes token0 and receives token1.
            return toBalanceDelta(-int128(int256(amountIn)), int128(int256(amountOut)));
        }
        // Router owes token1 and receives token0.
        return toBalanceDelta(int128(int256(amountOut)), -int128(int256(amountIn)));
    }

    function sync(Currency) external {}

    function settle() external payable returns (uint256 paid) {
        return msg.value;
    }

    function settleFor(address) external payable returns (uint256 paid) {
        return msg.value;
    }

    function take(Currency currency, address to, uint256 amount) external {
        IERC20Like(Currency.unwrap(currency)).transfer(to, amount);
    }
}
