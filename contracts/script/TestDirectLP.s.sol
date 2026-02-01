// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {console} from "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {LiquidityAmounts} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";

interface IPermit2 {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

/// @title DirectLP Test - Add liquidity directly to V4 pool without broker.executeWithApproval
/// @notice This tests LP flow by using deployer as token source instead of broker
contract TestDirectLP is Script, StdCheats {
    using StateLibrary for IPoolManager;

    // V4 Infrastructure
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant POSM = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    address constant TWAMM = 0x8E894E20a38B89C004E4FF5691553B08e8e52ac0;
    
    // RLD Infrastructure
    address constant BROKER_FACTORY = 0x87C7685147A150A069628479bEc5748f491B5cA0;
    bytes32 constant MARKET_ID = 0x6b63870a1260fcb989a7459c5e537d9f8a1f76890dd88927f105de03d513b33c;
    
    // Tokens
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant WRLP = 0x776d54ec60D1DDE9190E75F14b896cCe2CEaaC6c;
    
    // Aave
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    
    // Pool parameters  
    int24 constant TICK_LOWER = 6930;   // ~$2
    int24 constant TICK_UPPER = 29960;  // ~$20
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;
    
    address broker;
    
    function run() external {
        console.log("====== DIRECT LP TEST ======");
        
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        console.log("Deployer:", deployer);
        
        // Step 1: Get USDC and deposit to Aave
        console.log("Step 1: Getting aUSDC...");
        vm.startBroadcast(deployerKey);
        
        deal(USDC, deployer, 10_000_000 * 1e6);  // 10M USDC
        IERC20(USDC).approve(AAVE_POOL, type(uint256).max);
        IAavePool(AAVE_POOL).supply(USDC, 10_000_000 * 1e6, deployer, 0);
        
        vm.stopBroadcast();
        console.log("  aUSDC:", IERC20(AUSDC).balanceOf(deployer) / 1e6);
        
        // Step 2: Deploy broker and mint wRLP properly
        console.log("Step 2: Minting wRLP via broker...");
        vm.startBroadcast(deployerKey);
        
        // Deploy broker
        broker = PrimeBrokerFactory(BROKER_FACTORY).createBroker(keccak256(abi.encode(block.timestamp, deployer)));
        
        // Transfer aUSDC to broker
        uint256 collateralAmount = 5_000_000 * 1e6;  // 5M aUSDC as collateral
        IERC20(AUSDC).transfer(broker, collateralAmount);
        
        // Mint wRLP via broker (20% LTV -> 1M debt -> ~200k wRLP at ~$5)
        uint256 wRLPToMint = 200_000 * 1e6;  // 200k wRLP (6 decimals)
        PrimeBroker(payable(broker)).modifyPosition(
            MARKET_ID,
            int256(collateralAmount),
            int256(wRLPToMint)
        );
        
        vm.stopBroadcast();
        console.log("  Broker wRLP:", IERC20(WRLP).balanceOf(broker) / 1e6);
        
        // Step 3: Withdraw tokens from broker to deployer
        console.log("Step 3: Withdrawing tokens to deployer...");
        vm.startBroadcast(deployerKey);
        
        // Withdraw wRLP
        uint256 wRLPForLP = 50_000 * 1e6;  // 50k wRLP for LP
        PrimeBroker(payable(broker)).withdrawPositionToken(deployer, wRLPForLP);
        
        // Transfer remaining aUSDC from deployer to deployer (already have it)
        
        vm.stopBroadcast();
        
        console.log("  Deployer wRLP:", IERC20(WRLP).balanceOf(deployer) / 1e6);
        console.log("  Deployer aUSDC:", IERC20(AUSDC).balanceOf(deployer) / 1e6);
        
        // Step 4: Approve Permit2
        console.log("Step 4: Setting up Permit2 approvals...");
        vm.startBroadcast(deployerKey);
        
        IERC20(WRLP).approve(PERMIT2, type(uint256).max);
        IERC20(AUSDC).approve(PERMIT2, type(uint256).max);
        IPermit2(PERMIT2).approve(WRLP, POSM, type(uint160).max, type(uint48).max);
        IPermit2(PERMIT2).approve(AUSDC, POSM, type(uint160).max, type(uint48).max);
        
        vm.stopBroadcast();
        console.log("  Approvals done!");
        
        // Step 5: Call POSM directly
        console.log("Step 5: Adding V4 liquidity directly...");
        
        uint256 wRLPAmount = 10_000 * 1e6;   // 10k wRLP
        uint256 aUSDCAmount = 50_000 * 1e6;  // 50k aUSDC
        
        bytes memory unlockData = _buildPOSMData(deployer, wRLPAmount, aUSDCAmount);
        
        vm.startBroadcast(deployerKey);
        IPositionManager(POSM).modifyLiquidities(unlockData, block.timestamp + 600);
        vm.stopBroadcast();
        
        console.log("  SUCCESS! LP added!");
        
        // Check NFT
        uint256 tokenId = IPositionManager(POSM).nextTokenId() - 1;
        console.log("  Token ID:", tokenId);
        
        console.log("====== FINAL STATE ======");
        console.log("Deployer wRLP:", IERC20(WRLP).balanceOf(deployer) / 1e6);
        console.log("Deployer aUSDC:", IERC20(AUSDC).balanceOf(deployer) / 1e6);
    }
    
    function _buildPOSMData(address recipient, uint256 wRLPAmount, uint256 aUSDCAmount) internal view returns (bytes memory) {
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(WRLP),
            currency1: Currency.wrap(AUSDC),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(TWAMM)
        });
        
        // Get current sqrt price
        (uint160 sqrtPriceX96,,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        
        // Calculate liquidity
        uint160 sqrtLower = TickMath.getSqrtPriceAtTick(TICK_LOWER);
        uint160 sqrtUpper = TickMath.getSqrtPriceAtTick(TICK_UPPER);
        
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPriceX96,
            sqrtLower,
            sqrtUpper,
            wRLPAmount,
            aUSDCAmount
        );
        
        console.log("  Liquidity:", liquidity);
        
        // Use SETTLE_PAIR for simplicity
        bytes memory actions = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );
        
        bytes[] memory params = new bytes[](2);
        params[0] = abi.encode(
            poolKey, TICK_LOWER, TICK_UPPER,
            uint256(liquidity),
            uint128(wRLPAmount * 105 / 100),  // 5% slippage
            uint128(aUSDCAmount * 105 / 100),
            recipient, bytes("")
        );
        params[1] = abi.encode(Currency.wrap(WRLP), Currency.wrap(AUSDC));
        
        return abi.encode(actions, params);
    }
}
