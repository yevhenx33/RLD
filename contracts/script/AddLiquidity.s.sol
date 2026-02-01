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

/// @title AddLiquidityScript
/// @notice Adds concentrated liquidity to the wRLP/aUSDC V4 pool
/// @dev Called from shell script after tokens are approved
contract AddLiquidityScript is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // ─── MAINNET ADDRESSES ───────────────────────────────────────────────
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant V4_POSITION_MANAGER = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    
    // Fee tier
    int24 constant TICK_SPACING = 5; // Pool was created with tick spacing 5
    uint24 constant FEE = 500;        // 0.05%

    function run() external {
        // Read environment/arguments
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        uint256 wrlpAmount = vm.envUint("WRLP_AMOUNT");
        uint256 ausdcAmount = vm.envUint("AUSDC_AMOUNT");
        
        console.log("=== AddLiquidity Script ===");
        console.log("Position Token (wRLP):", positionToken);
        console.log("TWAMM Hook:", twammHook);
        console.log("wRLP Amount:", wrlpAmount / 1e6);
        console.log("aUSDC Amount:", ausdcAmount / 1e6);
        
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        
        // ─── CONSTRUCT POOL KEY ──────────────────────────────────────────
        // Sort currencies - V4 requires currency0 < currency1
        (address currency0Addr, address currency1Addr) = positionToken < AUSDC 
            ? (positionToken, AUSDC) 
            : (AUSDC, positionToken);
            
        bool wrlpIsCurrency0 = positionToken < AUSDC;
        
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twammHook)
        });
        
        console.log("Currency0:", currency0Addr);
        console.log("Currency1:", currency1Addr);
        console.log("wRLP is currency0:", wrlpIsCurrency0);
        
        // ─── GET CURRENT POOL STATE ──────────────────────────────────────
        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        (uint160 sqrtPriceX96, int24 currentTick,,) = pm.getSlot0(poolKey.toId());
        
        console.log("Current sqrtPriceX96:", sqrtPriceX96);
        console.log("Current tick:", int256(currentTick));
        
        // ─── CALCULATE TICK RANGE FOR aUSDC/wRLP PRICE 2-20 ─────────────
        // V4 price = token1/token0 = wRLP/aUSDC
        // 
        // User wants aUSDC/wRLP in [2, 20]
        // => wRLP/aUSDC in [1/20, 1/2] = [0.05, 0.5]
        //
        // tick = ln(price) / ln(1.0001)
        // For wRLP/aUSDC = 0.05: tick = ln(0.05) / ln(1.0001) ≈ -29957
        // For wRLP/aUSDC = 0.5:  tick = ln(0.5) / ln(1.0001)  ≈ -6931
        //
        // Current tick is -15660 which IS within this range!
        // This means current aUSDC/wRLP price ≈ 4.7 (within [2,20])
        
        // Tick range for aUSDC/wRLP price in [2, 20]
        int24 tickLower = -29955; // aUSDC/wRLP = 20 (wRLP/aUSDC = 0.05)
        int24 tickUpper = -6930;  // aUSDC/wRLP = 2  (wRLP/aUSDC = 0.5)
        
        // Align to tick spacing (5)
        tickLower = (tickLower / TICK_SPACING) * TICK_SPACING;
        tickUpper = (tickUpper / TICK_SPACING) * TICK_SPACING;
        
        console.log("Tick lower:", int256(tickLower));
        console.log("Tick upper:", int256(tickUpper));
        console.log("Current tick in range:", currentTick >= tickLower && currentTick <= tickUpper);
        
        // ─── CALCULATE LIQUIDITY ─────────────────────────────────────────
        // Use both amounts to compute optimal liquidity
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPriceX96,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            wrlpIsCurrency0 ? wrlpAmount : ausdcAmount,
            wrlpIsCurrency0 ? ausdcAmount : wrlpAmount
        );
        
        console.log("Calculated liquidity:", liquidity);
        
        // ─── BUILD ACTIONS ───────────────────────────────────────────────
        vm.startBroadcast(deployerKey);
        
        // Build the action plan:
        // 1. MINT_POSITION - create the LP position
        // 2. CLOSE_CURRENCY - settle currency0
        // 3. CLOSE_CURRENCY - settle currency1
        
        // Use MINT_POSITION_FROM_DELTAS which calculates liquidity from actual available tokens
        // This avoids rebasing issues by working with actual balances
        bytes memory actions = new bytes(4);
        actions[0] = bytes1(uint8(Actions.SETTLE_PAIR));     // First deposit tokens
        actions[1] = bytes1(uint8(Actions.MINT_POSITION_FROM_DELTAS)); // Mint from available deltas
        actions[2] = bytes1(uint8(Actions.CLOSE_CURRENCY));  // Close remaining currency0
        actions[3] = bytes1(uint8(Actions.CLOSE_CURRENCY));  // Close remaining currency1
        
        bytes[] memory params = new bytes[](4);
        
        // SETTLE_PAIR: deposit both tokens first
        params[0] = abi.encode(poolKey.currency0, poolKey.currency1);
        
        // MINT_POSITION_FROM_DELTAS: poolKey, tickLower, tickUpper, amount0Max, amount1Max, recipient, hookData
        params[1] = abi.encode(
            poolKey,
            tickLower,
            tickUpper,
            type(uint128).max, // amount0Max
            type(uint128).max, // amount1Max
            deployer,          // recipient
            bytes("")          // hookData
        );
        
        // CLOSE_CURRENCY for refunds
        params[2] = abi.encode(poolKey.currency0);
        params[3] = abi.encode(poolKey.currency1);
        
        // Encode the unlock data
        bytes memory unlockData = abi.encode(actions, params);
        
        // Call modifyLiquidities
        console.log("Calling modifyLiquidities...");
        IPositionManager posm = IPositionManager(V4_POSITION_MANAGER);
        posm.modifyLiquidities(unlockData, block.timestamp + 1 hours);
        
        vm.stopBroadcast();
        
        // ─── VERIFY RESULT ───────────────────────────────────────────────
        uint256 tokenId = posm.nextTokenId() - 1;
        console.log("=== LP Position Created! ===");
        console.log("Token ID:", tokenId);
        console.log("Owner:", deployer);
    }
}
