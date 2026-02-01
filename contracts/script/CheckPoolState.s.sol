// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";

contract CheckPoolState is Script {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    function run() external view {
        string memory json = vm.readFile("deployments.json");
        address CORE = vm.parseJsonAddress(json, ".RLDCore");
        address POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
        address TWAMM_HOOK = vm.parseJsonAddress(json, ".TWAMM");
        bytes32 MARKET_ID = vm.parseJsonBytes32(json, ".MarketId");

        IRLDCore core = IRLDCore(CORE);
        IPoolManager pm = IPoolManager(POOL_MANAGER);

        IRLDCore.MarketAddresses memory addrs = core.getMarketAddresses(MarketId.wrap(MARKET_ID));
        
        address tokenA = addrs.positionToken;
        address tokenB = addrs.collateralToken;
        
        Currency currency0 = Currency.wrap(tokenA);
        Currency currency1 = Currency.wrap(tokenB);
        if (currency0 > currency1) {
            (currency0, currency1) = (currency1, currency0);
        }

        PoolKey memory key = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: 500,
            tickSpacing: 5,
            hooks: IHooks(TWAMM_HOOK)
        });

        (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee) = pm.getSlot0(key.toId());
        uint128 liquidity = pm.getLiquidity(key.toId());

        console.log("=== V4 POOL STATE ===");
        console.log("Market ID:", vm.toString(MARKET_ID));
        console.log("Token0:", Currency.unwrap(currency0));
        console.log("Token1:", Currency.unwrap(currency1));
        console.log("SqrtPriceX96:", sqrtPriceX96);
        console.log("Tick:", tick);
        console.log("Liquidity:", liquidity);
        console.log("Protocol Fee:", protocolFee);
        console.log("LP Fee:", lpFee);
        
        uint256 priceX96 = uint256(sqrtPriceX96) * uint256(sqrtPriceX96);
        uint256 price = (priceX96 * 1e18) >> (96 * 2);
        
        console.log("Price (Token1/Token0):", price);
        
        if (Currency.unwrap(currency0) == tokenA) {
            console.log("Direction: Collateral per wRLP");
        } else {
            console.log("Direction: wRLP per Collateral");
            if (price > 0) {
                console.log("Inverted Price (Collateral per wRLP):", (1e36) / price);
            }
        }
    }
}
