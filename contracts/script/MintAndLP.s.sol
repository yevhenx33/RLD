// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";

/// @title MintAndLPScript
/// @notice Opens a position (mints wRLP) and provides concentrated liquidity to V4 pool
contract MintAndLPScript is Script {
    using StateLibrary for IPoolManager;

    // ─── MAINNET ADDRESSES ───────────────────────────────────────────────
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant V4_POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant V4_POSITION_MANAGER = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    
    // Default whale for impersonation
    address constant USDC_WHALE = 0xCFFAd3200574698b78f32232aa9D63eABD290703;

    // ─── CONFIGURATION ───────────────────────────────────────────────────
    uint256 public constant COLLATERAL_AMOUNT = 5_000_000e6; // 5M aUSDC
    uint256 public constant DEBT_AMOUNT = 100_000e6;         // 100k wRLP
    uint256 public constant LP_AMOUNT = 50_000e6;            // 50k of each token for LP
    
    // Tick range for concentrated liquidity (-5% to +5% from current price)
    int24 public constant TICK_SPACING = 10; // Standard for 0.05% fee tier
    int24 public constant TICK_RANGE = 500;  // ~5% range on each side

    function run() external {
        // Load addresses from deployments.json
        string memory deployments = vm.readFile("deployments.json");
        
        address brokerFactory = vm.parseJsonAddress(deployments, ".BrokerFactory");
        bytes32 marketId = vm.parseJsonBytes32(deployments, ".MarketId");
        address positionToken = vm.parseJsonAddress(deployments, ".PositionToken");
        address twamm = vm.parseJsonAddress(deployments, ".TWAMM");
        
        console.log("=== Configuration ===");
        console.log("Broker Factory:", brokerFactory);
        console.log("Position Token (wRLP):", positionToken);
        console.log("TWAMM Hook:", twamm);
        
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        
        // ─── STEP 1: ACQUIRE aUSDC (via whale impersonation) ─────────────
        console.log("\n=== Step 1: Acquiring aUSDC from whale ===");
        
        // Impersonate whale and deposit to Aave
        vm.startPrank(USDC_WHALE);
        ERC20(USDC).approve(AAVE_POOL, COLLATERAL_AMOUNT);
        (bool success,) = AAVE_POOL.call(
            abi.encodeWithSignature(
                "supply(address,uint256,address,uint16)",
                USDC, COLLATERAL_AMOUNT, deployer, 0
            )
        );
        require(success, "Aave supply failed");
        vm.stopPrank();
        
        uint256 aUsdcBalance = ERC20(AUSDC).balanceOf(deployer);
        console.log("Deployer aUSDC balance:", aUsdcBalance / 1e6, "aUSDC");
        
        // ─── STEP 2: ADVANCE TIME FOR TWAMM ORACLE ───────────────────────
        console.log("\n=== Step 2: Priming TWAMM oracle ===");
        vm.warp(block.timestamp + 2 hours);
        console.log("Advanced time by 2 hours");
        
        // ─── STEP 3: CREATE BROKER ───────────────────────────────────────
        console.log("\n=== Step 3: Creating PrimeBroker ===");
        
        vm.startBroadcast(deployerKey);
        
        bytes32 salt = keccak256(abi.encodePacked("lp-broker-", block.timestamp));
        (bool createSuccess, bytes memory result) = brokerFactory.call(
            abi.encodeWithSignature("createBroker(bytes32)", salt)
        );
        require(createSuccess, "Broker creation failed");
        address broker = abi.decode(result, (address));
        console.log("Broker created:", broker);
        
        // ─── STEP 4: TRANSFER COLLATERAL TO BROKER ───────────────────────
        console.log("\n=== Step 4: Transferring collateral ===");
        ERC20(AUSDC).transfer(broker, aUsdcBalance);
        console.log("Transferred", aUsdcBalance / 1e6, "aUSDC to broker");
        
        // ─── STEP 5: MINT WRLP DEBT ──────────────────────────────────────
        console.log("\n=== Step 5: Minting wRLP debt ===");
        (bool mintSuccess,) = broker.call(
            abi.encodeWithSignature(
                "modifyPosition(bytes32,int256,int256)",
                marketId, int256(0), int256(DEBT_AMOUNT)
            )
        );
        require(mintSuccess, "Position mint failed");
        
        uint256 wrlpBalance = ERC20(positionToken).balanceOf(broker);
        console.log("Broker wRLP balance:", wrlpBalance / 1e6, "wRLP");
        
        // ─── STEP 6: WITHDRAW WRLP FROM BROKER ───────────────────────────
        console.log("\n=== Step 6: Withdrawing wRLP for LP ===");
        
        // Withdraw half of wRLP for LP
        uint256 wrlpForLp = LP_AMOUNT;
        (bool withdrawSuccess,) = broker.call(
            abi.encodeWithSignature(
                "withdraw(address,uint256)",
                positionToken, wrlpForLp
            )
        );
        require(withdrawSuccess, "wRLP withdraw failed");
        console.log("Withdrawn", wrlpForLp / 1e6, "wRLP to deployer");
        
        // Also need aUSDC for LP - withdraw from broker
        uint256 aUsdcForLp = LP_AMOUNT;
        (bool withdrawAusdcSuccess,) = broker.call(
            abi.encodeWithSignature(
                "withdraw(address,uint256)",
                AUSDC, aUsdcForLp
            )
        );
        require(withdrawAusdcSuccess, "aUSDC withdraw failed");
        console.log("Withdrawn", aUsdcForLp / 1e6, "aUSDC to deployer");
        
        vm.stopBroadcast();
        
        // ─── STEP 7: PROVIDE LIQUIDITY TO V4 POOL ────────────────────────
        console.log("\n=== Step 7: Adding concentrated liquidity to V4 pool ===");
        
        // Get current pool tick
        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        
        // Construct PoolKey for the wRLP/aUSDC pool
        // Note: currencies are sorted by address
        (address currency0, address currency1) = positionToken < AUSDC 
            ? (positionToken, AUSDC) 
            : (AUSDC, positionToken);
            
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0),
            currency1: Currency.wrap(currency1),
            fee: 500, // 0.05% fee tier
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twamm)
        });
        
        // Get current tick
        (, int24 currentTick,,) = pm.getSlot0(poolKey.toId());
        console.log("Current tick:", int256(currentTick));
        
        // Calculate tick range (centered on current tick, aligned to spacing)
        int24 tickLower = ((currentTick - TICK_RANGE) / TICK_SPACING) * TICK_SPACING;
        int24 tickUpper = ((currentTick + TICK_RANGE) / TICK_SPACING) * TICK_SPACING;
        console.log("LP tick lower:", int256(tickLower));
        console.log("LP tick upper:", int256(tickUpper));
        
        vm.startBroadcast(deployerKey);
        
        // Approve Permit2 for both tokens
        ERC20(positionToken).approve(PERMIT2, type(uint256).max);
        ERC20(AUSDC).approve(PERMIT2, type(uint256).max);
        
        // Approve PositionManager via Permit2
        (bool approveSuccess,) = PERMIT2.call(
            abi.encodeWithSignature(
                "approve(address,address,uint160,uint48)",
                positionToken, V4_POSITION_MANAGER, type(uint160).max, type(uint48).max
            )
        );
        require(approveSuccess, "Permit2 approve wRLP failed");
        
        (approveSuccess,) = PERMIT2.call(
            abi.encodeWithSignature(
                "approve(address,address,uint160,uint48)",
                AUSDC, V4_POSITION_MANAGER, type(uint160).max, type(uint48).max
            )
        );
        require(approveSuccess, "Permit2 approve aUSDC failed");
        
        // Encode MINT_POSITION action
        // Parameters: poolKey, tickLower, tickUpper, liquidity, amount0Max, amount1Max, recipient, hookData
        uint128 liquidity = 1e18; // Placeholder - would calculate based on amounts
        
        // Build the plan: MINT_POSITION + CLOSE_CURRENCY x2
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
            type(uint128).max, // amount0Max
            type(uint128).max, // amount1Max
            deployer,          // recipient
            bytes("")          // hookData
        );
        params[1] = abi.encode(poolKey.currency0);
        params[2] = abi.encode(poolKey.currency1);
        
        bytes memory unlockData = abi.encode(actions, params);
        
        // Call modifyLiquidities
        IPositionManager posm = IPositionManager(V4_POSITION_MANAGER);
        posm.modifyLiquidities(unlockData, block.timestamp + 1 hours);
        
        vm.stopBroadcast();
        
        console.log("\n=== LP Position Created! ===");
        console.log("Check V4 PositionManager for your LP NFT");
    }
}
