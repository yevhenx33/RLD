// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ISpotOracle} from "../../../shared/interfaces/ISpotOracle.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {Owned} from "solmate/src/auth/Owned.sol";
import {IGhostRouter} from "../../../dex/interfaces/IGhostRouter.sol";

/// @title GhostSingletonOracle
/// @notice Single Oracle Contract that manages TWAP queries for multiple GhostRouter markets.
/// @dev Optimized to save gas by avoiding per-market Oracle Adapter deployment.
/// @dev Reads from the native price accumulator within the GhostRouter.
contract GhostSingletonOracle is ISpotOracle, Owned {
    struct PoolSettings {
        PoolKey key;
        PoolId poolId;
        bytes32 marketId;
        IGhostRouter router;
        uint32 period;
        bool set;
    }

    // Mapping from Position Token (wRLP) -> Pool Settings
    mapping(address => PoolSettings) public poolSettings;

    error InvalidTokens();
    error PoolNotRegistered();
    error InvalidPool();

    constructor() Owned(msg.sender) {}

    /// @notice Registers a GhostRouter market for mark price queries.
    function registerPool(
        address positionToken,
        PoolKey memory key,
        address routerAddr,
        uint32 period
    ) external onlyOwner {
        poolSettings[positionToken] = PoolSettings({
            key: key,
            poolId: key.toId(),
            marketId: PoolId.unwrap(key.toId()),
            router: IGhostRouter(routerAddr),
            period: period,
            set: true
        });
    }

    function getSpotPrice(
        address collateralToken,
        address underlyingToken
    ) external view override returns (uint256 price) {
        // collateralToken here serves as the key (Position Token)
        PoolSettings storage settings = poolSettings[collateralToken];

        if (!settings.set) {
            revert PoolNotRegistered();
        }

        address token0 = Currency.unwrap(settings.key.currency0);
        address token1 = Currency.unwrap(settings.key.currency1);

        // Check if query matches the pool's tokens
        bool zeroForOne = (collateralToken == token0 &&
            underlyingToken == token1);
        bool oneForZero = (collateralToken == token1 &&
            underlyingToken == token0);

        if (!zeroForOne && !oneForZero) {
            revert InvalidTokens();
        }

        uint32[] memory secondsAgos = new uint32[](2);
        secondsAgos[0] = settings.period;
        secondsAgos[1] = 0;

        uint256[] memory priceCumulatives = settings.router.observe(
            settings.marketId,
            secondsAgos
        );

        uint256 cumulativeDelta = priceCumulatives[1] - priceCumulatives[0];
        uint256 twapPriceRaw = cumulativeDelta / settings.period;

        uint8 decimals0 = ERC20(token0).decimals();
        uint8 decimals1 = ERC20(token1).decimals();

        if (zeroForOne) {
            price = (twapPriceRaw * (10 ** decimals0)) / (10 ** decimals1);
        } else {
            // Need token0 per token1. twapPriceRaw is token1/token0 * 1e18.
            // Inverse in 1e18 = 1e36 / twapPriceRaw
            uint256 invTwapPriceRaw = 1e36 / twapPriceRaw;
            price = (invTwapPriceRaw * (10 ** decimals1)) / (10 ** decimals0);
        }
    }
}
