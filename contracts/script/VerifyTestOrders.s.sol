// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {IJTM} from "../src/twamm/IJTM.sol";
import {JTM} from "../src/twamm/JTM.sol";

/**
 * @title VerifyTestOrders
 * @notice Reads the state of 4 test orders placed by PlaceTestOrders.s.sol
 *         and computes value preservation metrics.
 *
 * Usage:
 *   WAUSDC=... POSITION_TOKEN=... TWAMM_HOOK=... \
 *     forge script script/VerifyTestOrders.s.sol --rpc-url http://localhost:8545
 */
contract VerifyTestOrders is Script {
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;
    uint256 constant RATE_SCALER = 1e18;

    // Anvil accounts 6-9 addresses
    address constant ACC6 = 0xD0f6cd71c0f8bB25F381be80b690b535c868a6f5;
    address constant ACC7 = 0x71bE63f3384f5fb98995898A86B02Fb2426c5788;
    address constant ACC8 = 0xFABB0ac9d68B0B445fB7357272Ff202C5651694a;
    address constant ACC9 = 0x1CBd3b2770909D4e10f157cABC84C7264073C9Ec;

    struct OrderResult {
        string label;
        address owner;
        uint160 expiration;
        bool zeroForOne;
        uint256 deposit; // in sell token (6 dec)
        uint256 buyTokensOwed; // from getCancelOrderState
        uint256 sellTokensRefund;
        uint256 sellRate;
        uint256 ghostShare; // pro-rata ghost attribution
        uint256 discountBps;
    }

    function run() external view {
        address waUSDC = vm.envAddress("WAUSDC");
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address hook = vm.envAddress("TWAMM_HOOK");

        // Build pool key
        (address c0, address c1) = waUSDC < positionToken
            ? (waUSDC, positionToken)
            : (positionToken, waUSDC);
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(c0),
            currency1: Currency.wrap(c1),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(hook)
        });
        bool waUSDCisC0 = (waUSDC == c0);
        IJTM jtm = IJTM(hook);

        console.log("============================================");
        console.log("  ORDER VERIFICATION REPORT");
        console.log("  Block timestamp:", block.timestamp);
        console.log("============================================");

        // ── Stream state ──
        (
            uint256 accrued0,
            uint256 accrued1,
            uint256 discountBps,
            uint256 timeSinceClear
        ) = jtm.getStreamState(poolKey);
        console.log("");
        console.log("Stream State:");
        console.log("  accrued0:       ", accrued0);
        console.log("  accrued1:       ", accrued1);
        console.log("  discountBps:    ", discountBps);
        console.log("  timeSinceClear: ", timeSinceClear);

        // ── Stream pools ──
        (uint256 sr0For1, uint256 ef0For1) = jtm.getStreamPool(poolKey, true);
        (uint256 sr1For0, uint256 ef1For0) = jtm.getStreamPool(poolKey, false);
        console.log("");
        console.log("Stream Pools:");
        console.log(
            "  0For1 sellRate:",
            sr0For1 / RATE_SCALER,
            "earningsFactor:",
            ef0For1
        );
        console.log(
            "  1For0 sellRate:",
            sr1For0 / RATE_SCALER,
            "earningsFactor:",
            ef1For0
        );

        // ── Compute interval boundary for expiration ──
        uint256 interval = JTM(hook).expirationInterval();
        uint256 expiration = ((block.timestamp / interval) + 1) * interval;
        console.log("  Expected expiration:", expiration);

        // ── Order 1: Account 6, sell waUSDC → buy wRLP ──
        _verifyOrder(
            jtm,
            poolKey,
            "Order 1 (1000 waUSDC -> wRLP)",
            ACC6,
            uint160(expiration),
            waUSDCisC0,
            1000e6,
            waUSDCisC0 ? accrued0 : accrued1, // ghost is in sell token direction
            waUSDCisC0 ? sr0For1 : sr1For0,
            discountBps,
            true // sells waUSDC
        );

        // ── Order 2: Account 7, sell wRLP → buy waUSDC ──
        _verifyOrder(
            jtm,
            poolKey,
            "Order 2 (300 wRLP -> waUSDC)",
            ACC7,
            uint160(expiration),
            !waUSDCisC0,
            300e6,
            waUSDCisC0 ? accrued1 : accrued0,
            waUSDCisC0 ? sr1For0 : sr0For1,
            discountBps,
            false // sells wRLP
        );

        // ── Order 3: Account 8, sell waUSDC → buy wRLP ──
        _verifyOrder(
            jtm,
            poolKey,
            "Order 3 (100 waUSDC -> wRLP)",
            ACC8,
            uint160(expiration),
            waUSDCisC0,
            100e6,
            waUSDCisC0 ? accrued0 : accrued1,
            waUSDCisC0 ? sr0For1 : sr1For0,
            discountBps,
            true
        );

        // ── Order 4: Account 9, sell wRLP → buy waUSDC ──
        _verifyOrder(
            jtm,
            poolKey,
            "Order 4 (50 wRLP -> waUSDC)",
            ACC9,
            uint160(expiration),
            !waUSDCisC0,
            50e6,
            waUSDCisC0 ? accrued1 : accrued0,
            waUSDCisC0 ? sr1For0 : sr0For1,
            discountBps,
            false
        );

        // ── Hook solvency ──
        console.log("");
        console.log("============================================");
        console.log("  HOOK SOLVENCY");
        console.log("============================================");
        uint256 hookBal0 = ERC20(c0).balanceOf(hook);
        uint256 hookBal1 = ERC20(c1).balanceOf(hook);
        console.log("  Hook token0 balance:", hookBal0);
        console.log("  Hook token1 balance:", hookBal1);
        // NOTE: collectedDust removed — auto-settle eliminates dust entirely
    }

    function _verifyOrder(
        IJTM jtm,
        PoolKey memory poolKey,
        string memory label,
        address owner,
        uint160 expiration,
        bool zeroForOne,
        uint256 originalDeposit,
        uint256 totalGhostInDirection,
        uint256 streamSellRate,
        uint256 discountBps,
        bool sellsWaUSDC
    ) internal view {
        IJTM.OrderKey memory orderKey = IJTM.OrderKey({
            owner: owner,
            expiration: expiration,
            zeroForOne: zeroForOne,
            nonce: 0
        });

        // Get order state
        IJTM.Order memory order = jtm.getOrder(poolKey, orderKey);
        if (order.sellRate == 0) {
            console.log("");
            console.log(label, "-> NOT FOUND (may have different expiration)");
            return;
        }

        (uint256 buyOwed, uint256 sellRefund) = jtm.getCancelOrderState(
            poolKey,
            orderKey
        );

        // Ghost attribution
        uint256 ghostShare = 0;
        if (streamSellRate > 0 && totalGhostInDirection > 0) {
            ghostShare =
                (totalGhostInDirection * order.sellRate) /
                streamSellRate;
        }
        uint256 discountedGhost = (ghostShare * (10000 - discountBps)) / 10000;

        // Compute time elapsed
        uint256 timeRemaining = 0;
        if (block.timestamp < expiration) {
            timeRemaining = expiration - block.timestamp;
        }
        uint256 timeElapsed = 3600 - timeRemaining; // 1h duration

        console.log("");
        console.log("--------------------------------------------");
        console.log(label);
        console.log("--------------------------------------------");
        console.log("  Owner:          ", owner);
        console.log("  Expiration:     ", expiration);
        console.log("  Time elapsed:   ", timeElapsed, "s");
        console.log("  Time remaining: ", timeRemaining, "s");
        console.log("  SellRate:       ", order.sellRate / RATE_SCALER);
        console.log("");
        console.log(
            "  Deposit:        ",
            originalDeposit,
            sellsWaUSDC ? "waUSDC" : "wRLP"
        );
        console.log(
            "  Buy tokens owed:",
            buyOwed,
            sellsWaUSDC ? "wRLP" : "waUSDC"
        );
        console.log(
            "  Sell refund:    ",
            sellRefund,
            sellsWaUSDC ? "waUSDC" : "wRLP"
        );
        console.log(
            "  Ghost share:    ",
            ghostShare,
            sellsWaUSDC ? "waUSDC" : "wRLP"
        );
        console.log("  Discounted ghost:", discountedGhost);
        console.log("  Discount:       ", discountBps, "bps");

        // Value preservation (approximate — we don't know exact price here)
        // For same-token terms: refund + discountedGhost (in sell token)
        // For cross-token terms: buyOwed (in buy token, needs price conversion)
        uint256 sameTokenValue = sellRefund + discountedGhost;
        console.log("");
        console.log("  VALUE SUMMARY:");
        console.log(
            "    Same-token (refund+ghost):",
            sameTokenValue,
            sellsWaUSDC ? "waUSDC" : "wRLP"
        );
        console.log(
            "    Cross-token (earned):     ",
            buyOwed,
            sellsWaUSDC ? "wRLP" : "waUSDC"
        );
        // Tokens spent = deposit - refund
        uint256 tokensSpent = originalDeposit > sellRefund
            ? originalDeposit - sellRefund
            : 0;
        console.log(
            "    Tokens spent so far:      ",
            tokensSpent,
            sellsWaUSDC ? "waUSDC" : "wRLP"
        );
    }
}
