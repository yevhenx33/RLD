// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {console} from "forge-std/console.sol";

import {IERC20} from "../src/shared/interfaces/IERC20.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

/// @title TestMintWRLP - Simple test for RLD position minting
/// @notice Verifies: Aave deposit → broker creation → collateral deposit → wRLP minting
contract TestMintWRLP is Script, StdCheats {
    // Tokens
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant WRLP = 0x776d54ec60D1DDE9190E75F14b896cCe2CEaaC6c;

    // Aave
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    // RLD Infrastructure
    address constant BROKER_FACTORY = 0x87C7685147A150A069628479bEc5748f491B5cA0;
    bytes32 constant MARKET_ID = 0x6b63870a1260fcb989a7459c5e537d9f8a1f76890dd88927f105de03d513b33c;

    function run() external {
        console.log("====== TEST: MINT wRLP ======");

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        console.log("Deployer:", deployer);

        vm.startBroadcast(deployerKey);

        // Step 1: Get USDC and deposit to Aave
        console.log("");
        console.log("Step 1: Getting aUSDC from Aave...");
        deal(USDC, deployer, 10_000 * 1e6); // 10k USDC
        console.log("  USDC balance:", IERC20(USDC).balanceOf(deployer) / 1e6);

        IERC20(USDC).approve(AAVE_POOL, type(uint256).max);
        IAavePool(AAVE_POOL).supply(USDC, 10_000 * 1e6, deployer, 0);

        uint256 aUSDCBal = IERC20(AUSDC).balanceOf(deployer);
        console.log("  aUSDC balance:", aUSDCBal / 1e6);

        // Step 2: Create broker
        console.log("");
        console.log("Step 2: Creating broker...");
        address broker =
            PrimeBrokerFactory(BROKER_FACTORY).createBroker(keccak256(abi.encode(block.timestamp, deployer, "test")));
        console.log("  Broker:", broker);

        // Step 3: Transfer aUSDC to broker
        console.log("");
        console.log("Step 3: Transferring aUSDC to broker...");
        uint256 collateralAmount = 5_000 * 1e6; // 5k aUSDC
        IERC20(AUSDC).transfer(broker, collateralAmount);
        console.log("  Broker aUSDC after transfer:", IERC20(AUSDC).balanceOf(broker) / 1e6);
        console.log("  Deployer aUSDC remaining:", IERC20(AUSDC).balanceOf(deployer) / 1e6);

        // Step 4: Deposit collateral and mint wRLP
        console.log("");
        console.log("Step 4: Opening position (deposit collateral + mint wRLP)...");

        // Target 20% LTV: 5k collateral -> 1k debt value -> ~200 wRLP at $5
        uint256 wRLPToMint = 200 * 1e6; // 200 wRLP (6 decimals)

        console.log("  Collateral to deposit:", collateralAmount / 1e6);
        console.log("  wRLP to mint:", wRLPToMint / 1e6);

        PrimeBroker(payable(broker)).modifyPosition(MARKET_ID, int256(collateralAmount), int256(wRLPToMint));

        // Step 5: Verify state after opening position
        console.log("");
        console.log("====== STATE AFTER OPEN ======");
        uint256 brokerAUSDCAfterOpen = IERC20(AUSDC).balanceOf(broker);
        uint256 brokerWRLPAfterOpen = IERC20(WRLP).balanceOf(broker);
        console.log("Broker aUSDC:", brokerAUSDCAfterOpen / 1e6);
        console.log("Broker wRLP:", brokerWRLPAfterOpen / 1e6);

        // ============================================================
        // STEP 6: CLOSE POSITION (Burn wRLP to repay debt)
        // ============================================================
        console.log("");
        console.log("Step 6: Closing position (burn wRLP to repay debt)...");

        // modifyPosition with negative deltaDebt burns wRLP and reduces debt
        PrimeBroker(payable(broker))
            .modifyPosition(
                MARKET_ID,
                int256(0), // No collateral change
                -int256(wRLPToMint) // Negative = repay/burn 200 wRLP
            );

        console.log("  Broker wRLP after close:", IERC20(WRLP).balanceOf(broker) / 1e6);

        // ============================================================
        // STEP 7: WITHDRAW COLLATERAL
        // ============================================================
        console.log("");
        console.log("Step 7: Withdrawing collateral back to deployer...");

        uint256 brokerCollateralBefore = IERC20(AUSDC).balanceOf(broker);
        console.log("  Broker aUSDC before withdraw:", brokerCollateralBefore / 1e6);

        // Use withdrawCollateral to get aUSDC back
        PrimeBroker(payable(broker)).withdrawCollateral(deployer, brokerCollateralBefore);

        console.log("  Broker aUSDC after withdraw:", IERC20(AUSDC).balanceOf(broker) / 1e6);
        console.log("  Deployer aUSDC after withdraw:", IERC20(AUSDC).balanceOf(deployer) / 1e6);

        // ============================================================
        // STEP 8: VERIFY ROUND-TRIP
        // ============================================================
        console.log("");
        console.log("====== FINAL STATE ======");
        uint256 deployerFinalAUSDC = IERC20(AUSDC).balanceOf(deployer);
        uint256 brokerFinalAUSDC = IERC20(AUSDC).balanceOf(broker);
        uint256 brokerFinalWRLP = IERC20(WRLP).balanceOf(broker);

        console.log("Deployer aUSDC (should be ~9999):", deployerFinalAUSDC / 1e6);
        console.log("Broker aUSDC (should be 0):", brokerFinalAUSDC / 1e6);
        console.log("Broker wRLP (should be 0):", brokerFinalWRLP / 1e6);

        // Verify assertions
        require(brokerFinalWRLP == 0, "Broker should have 0 wRLP after closing");
        require(brokerFinalAUSDC == 0, "Broker should have 0 aUSDC after withdrawal");
        require(deployerFinalAUSDC >= aUSDCBal - 1e6, "Deployer should get back ~all aUSDC");

        vm.stopBroadcast();

        console.log("");
        console.log("SUCCESS! Round-trip complete - collateral fully recovered.");
    }
}
