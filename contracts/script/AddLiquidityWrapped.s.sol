// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {LiquidityAmounts} from "@uniswap/v4-core/test/utils/LiquidityAmounts.sol";

/**
 * @title AddLiquidityWrappedScript
 * @notice Adds concentrated liquidity to the waUSDC/wRLP V4 pool
 * @dev Uses non-rebasing waUSDC to avoid settlement issues
 */
contract AddLiquidityWrappedScript is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // V4 addresses
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant V4_POSITION_MANAGER = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    
    // Pool params - must match market creation
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        // Read from environment
        address waUSDC = vm.envAddress("WAUSDC");
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        uint256 waUsdcAmount = vm.envUint("AUSDC_AMOUNT");  // waUSDC amount
        uint256 wrlpAmount = vm.envUint("WRLP_AMOUNT");
        
        console.log("=== AddLiquidity (Wrapped) ===");
        console.log("waUSDC:", waUSDC);
        console.log("wRLP:", positionToken);
        console.log("TWAMM Hook:", twammHook);
        console.log("waUSDC Amount:", waUsdcAmount / 1e6);
        console.log("wRLP Amount:", wrlpAmount / 1e6);
        
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        
        // Sort currencies
        (address currency0Addr, address currency1Addr) = waUSDC < positionToken 
            ? (waUSDC, positionToken) 
            : (positionToken, waUSDC);
            
        bool waUsdcIsCurrency0 = waUSDC < positionToken;
        
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twammHook)
        });
        
        console.log("Currency0:", currency0Addr);
        console.log("Currency1:", currency1Addr);
        console.log("waUSDC is currency0:", waUsdcIsCurrency0);
        
        // Get pool state
        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        (uint160 sqrtPriceX96, int24 currentTick,,) = pm.getSlot0(poolKey.toId());
        
        console.log("Current sqrtPriceX96:", sqrtPriceX96);
        console.log("Current tick:", int256(currentTick));
        
        // ─── TICK RANGE for waUSDC/wRLP price 2-20 ─────────────────────────
        // If waUSDC is currency0: V4 price = wRLP/waUSDC
        //   Want waUSDC/wRLP in [2, 20] => wRLP/waUSDC in [0.05, 0.5]
        //   ticks: [-29955, -6930]
        // If wRLP is currency0: V4 price = waUSDC/wRLP
        //   Want waUSDC/wRLP in [2, 20] 
        //   ticks: [6930, 29960]
        
        int24 tickLower;
        int24 tickUpper;
        
        if (waUsdcIsCurrency0) {
            // price = wRLP/waUSDC, want waUSDC/wRLP in [2,20] => price in [0.05, 0.5]
            tickLower = -29955;
            tickUpper = -6930;
        } else {
            // price = waUSDC/wRLP, want in [2, 20]
            tickLower = 6930;
            tickUpper = 29960;
        }
        
        // Align to tick spacing
        tickLower = (tickLower / TICK_SPACING) * TICK_SPACING;
        tickUpper = (tickUpper / TICK_SPACING) * TICK_SPACING;
        
        console.log("Tick lower:", int256(tickLower));
        console.log("Tick upper:", int256(tickUpper));
        console.log("Current tick in range:", currentTick >= tickLower && currentTick <= tickUpper);
        
        // Calculate liquidity
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPriceX96,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            waUsdcIsCurrency0 ? waUsdcAmount : wrlpAmount,
            waUsdcIsCurrency0 ? wrlpAmount : waUsdcAmount
        );
        
        console.log("Calculated liquidity:", liquidity);
        
        // ─── BUILD ACTIONS ───────────────────────────────────────────────
        vm.startBroadcast(deployerKey);
        
        bytes memory actions = new bytes(3);
        actions[0] = bytes1(uint8(Actions.MINT_POSITION));
        actions[1] = bytes1(uint8(Actions.CLOSE_CURRENCY));
        actions[2] = bytes1(uint8(Actions.CLOSE_CURRENCY));
        
        bytes[] memory params = new bytes[](3);
        
        params[0] = abi.encode(
            poolKey,
            tickLower,
            tickUpper,
            liquidity,
            type(uint128).max,
            type(uint128).max,
            deployer,
            bytes("")
        );
        
        params[1] = abi.encode(poolKey.currency0);
        params[2] = abi.encode(poolKey.currency1);
        
        bytes memory unlockData = abi.encode(actions, params);
        
        console.log("Calling modifyLiquidities...");
        IPositionManager posm = IPositionManager(V4_POSITION_MANAGER);
        posm.modifyLiquidities(unlockData, block.timestamp + 1 hours);
        
        vm.stopBroadcast();
        
        // Verify
        uint256 tokenId = posm.nextTokenId() - 1;
        console.log("=== LP Position Created! ===");
        console.log("Token ID:", tokenId);
        console.log("Owner:", deployer);
    }
}
