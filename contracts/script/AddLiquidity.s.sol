// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";

import {IERC20} from "../src/shared/interfaces/IERC20.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../src/shared/interfaces/IRLDOracle.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";

import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {LiquidityAmounts} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

interface IPermit2 {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

/**
 * @title AddLiquidity
 * @notice Provisions liquidity into the wRLPaUSDC/aUSDC V4 pool with TWAMM hook
 */
contract AddLiquidity is Script, StdCheats {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;

    // ======================= ADDRESSES =======================
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant POSM = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    address constant CORE = 0x62e5c8AA289a610bd16d38fF49e46B038623B29f;
    address constant TWAMM = 0x8E894E20a38B89C004E4FF5691553B08e8e52ac0;
    address constant AAVE_ORACLE = 0x475102156b26305510F56234C6c9D21130FCFC4a;
    bytes32 constant MARKET_ID = 0x6b63870a1260fcb989a7459c5e537d9f8a1f76890dd88927f105de03d513b33c;
    address constant WRLP = 0x776d54ec60D1DDE9190E75F14b896cCe2CEaaC6c;
    address constant BROKER_FACTORY = 0x87C7685147A150A069628479bEc5748f491B5cA0;
    
    // ======================= CONFIG =======================
    uint256 constant TOTAL_USDC = 10_000_000e6;
    int24 constant TICK_LOWER = 6930;
    int24 constant TICK_UPPER = 29960;
    uint24 constant POOL_FEE = 500;
    int24 constant TICK_SPACING = 5;

    // ======================= STATE =======================
    address public broker;
    uint256 public wRLPAmount;
    uint256 public aUSDCAmount;
    
    function run() external {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address lpProvider = vm.addr(deployerKey);
        
        console.log("====== ADD LIQUIDITY ======");
        console.log("Provider:", lpProvider);
        
        vm.startBroadcast(deployerKey);
        
        // Step 1-2: Get aUSDC
        _acquireAUSDC(lpProvider);
        
        // Step 3-4: Deploy broker and fund it
        uint256 aUSDCBal = IERC20(AUSDC).balanceOf(lpProvider);
        _setupBroker(lpProvider, aUSDCBal);
        
        // Step 5-6: Calculate and open position
        _openPosition(aUSDCBal);
        
        vm.stopBroadcast();
        
        // Step 7: Add V4 liquidity (requires vm.prank)
        _addV4Liquidity(deployerKey);
        
        // Step 8: Register LP
        vm.startBroadcast(deployerKey);
        _registerLP();
        vm.stopBroadcast();
        
        // Report
        _printFinalState();
    }
    
    function _acquireAUSDC(address lpProvider) internal {
        console.log("Step 1: Dealing USDC...");
        deal(USDC, lpProvider, TOTAL_USDC);
        
        console.log("Step 2: Depositing to Aave...");
        IERC20(USDC).approve(AAVE_POOL, TOTAL_USDC);
        IAavePool(AAVE_POOL).supply(USDC, TOTAL_USDC, lpProvider, 0);
        console.log("  aUSDC:", IERC20(AUSDC).balanceOf(lpProvider) / 1e6);
    }
    
    function _setupBroker(address lpProvider, uint256 aUSDCBal) internal {
        console.log("Step 3: Deploying broker...");
        bytes32 salt = keccak256(abi.encodePacked(lpProvider, block.timestamp));
        broker = PrimeBrokerFactory(BROKER_FACTORY).createBroker(salt);
        console.log("  Broker:", broker);
        
        console.log("Step 4: Funding broker...");
        IERC20(AUSDC).transfer(broker, aUSDCBal);
    }
    
    function _openPosition(uint256 aUSDCBal) internal {
        console.log("Step 5: Calculating 20% LTV...");
        uint256 indexPrice = IRLDOracle(AAVE_ORACLE).getIndexPrice(AAVE_POOL, USDC);
        console.log("  Index Price (WAD):", indexPrice);
        
        // Both wRLP and aUSDC use 6 decimals, indexPrice is WAD (18 decimals)
        // For 20% LTV: debtValue = collateral * 0.20
        // wRLP (6 dec) = debtValue (6 dec) * 1e18 / indexPrice (18 dec)
        uint256 debtValue = aUSDCBal * 20 / 100;  // 20% of collateral
        wRLPAmount = (debtValue * 1e18) / indexPrice;
        // aUSDC for LP = wRLP value in collateral terms + 50% buffer for settlement precision
        aUSDCAmount = ((wRLPAmount * indexPrice) / 1e18) * 150 / 100;
        
        console.log("  wRLP to mint:", wRLPAmount / 1e6);
        console.log("  aUSDC for LP:", aUSDCAmount / 1e6);
        uint256 collateralToDeposit = aUSDCBal - aUSDCAmount;  // Reserve aUSDC for LP
        console.log("  Collateral to deposit:", collateralToDeposit / 1e6);
        
        console.log("Step 6: Opening position...");
        PrimeBroker(payable(broker)).modifyPosition(
            MARKET_ID,
            int256(collateralToDeposit),  // Only deposit what we don't need for LP
            int256(wRLPAmount)
        );
    }
    
    function _addV4Liquidity(uint256 deployerKey) internal {
        console.log("Step 7: Adding V4 liquidity...");
        
        // Permit2 requires 2 approvals:
        // 1. ERC20 approve to Permit2 (allows Permit2 to pull tokens)
        // 2. Permit2.approve() to set internal per-spender allowance (allows specific spender to use Permit2)
        
        vm.startPrank(broker);
        
        // Step 1: ERC20 approve to Permit2
        IERC20(WRLP).approve(PERMIT2, type(uint256).max);
        IERC20(AUSDC).approve(PERMIT2, type(uint256).max);
        
        // Step 2: Set Permit2 internal allowance for POSM
        // Permit2.approve(token, spender, amount, expiration)
        IPermit2(PERMIT2).approve(WRLP, POSM, type(uint160).max, type(uint48).max);
        IPermit2(PERMIT2).approve(AUSDC, POSM, type(uint160).max, type(uint48).max);
        
        vm.stopPrank();
        
        // Build POSM call
        bytes memory unlockData = _buildPOSMData();
        
        vm.startBroadcast(deployerKey);
        PrimeBroker(payable(broker)).executeWithApproval(
            POSM,
            abi.encodeCall(IPositionManager.modifyLiquidities, (unlockData, block.timestamp + 600)),
            address(0),
            0
        );
        vm.stopBroadcast();
        
        console.log("  Liquidity added!");
    }
    
    function _buildPOSMData() internal view returns (bytes memory) {
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(WRLP),
            currency1: Currency.wrap(AUSDC),
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(TWAMM)
        });
        
        (uint160 sqrtPriceX96,,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPriceX96,
            TickMath.getSqrtPriceAtTick(TICK_LOWER),
            TickMath.getSqrtPriceAtTick(TICK_UPPER),
            wRLPAmount,
            aUSDCAmount
        );
        
        // Use CLOSE_CURRENCY for each token to handle ±1 wei rounding
        // CLOSE_CURRENCY settles if delta is negative (owes), takes if positive (credit)
        bytes memory actions = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.CLOSE_CURRENCY),  // Close wRLP delta
            uint8(Actions.CLOSE_CURRENCY)   // Close aUSDC delta
        );
        
        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(
            poolKey, TICK_LOWER, TICK_UPPER,
            uint256(liquidity),
            uint128(wRLPAmount * 105 / 100),
            uint128(aUSDCAmount * 105 / 100),
            broker, bytes("")
        );
        params[1] = abi.encode(Currency.wrap(WRLP));  // Close wRLP
        params[2] = abi.encode(Currency.wrap(AUSDC)); // Close aUSDC
        
        return abi.encode(actions, params);
    }
    
    function _registerLP() internal {
        console.log("Step 8: Registering LP position...");
        uint256 tokenId = IPositionManager(POSM).nextTokenId() - 1;
        PrimeBroker(payable(broker)).setActiveV4Position(tokenId);
        console.log("  Token ID:", tokenId);
    }
    
    function _printFinalState() internal view {
        console.log("====== FINAL STATE ======");
        console.log("Broker:", broker);
        console.log("wRLP:", IERC20(WRLP).balanceOf(broker) / 1e18);
        console.log("aUSDC:", IERC20(AUSDC).balanceOf(broker) / 1e6);
        
        bool solvent = IRLDCore(CORE).isSolvent(MarketId.wrap(MARKET_ID), broker);
        console.log("Solvent:", solvent ? "YES" : "NO");
    }
}
