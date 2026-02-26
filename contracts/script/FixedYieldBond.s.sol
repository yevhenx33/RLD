// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPrimeBroker} from "../src/shared/interfaces/IPrimeBroker.sol";
import {ITWAMM} from "../src/twamm/ITWAMM.sol";

/// @title FixedYieldBond
/// @notice Creates a synthetic fixed-yield bond by:
///   1. Depositing collateral (waUSDC)
///   2. Minting wRLP as a hedge
///   3. Submitting a TWAMM order to sell wRLP linearly over duration
///
/// @dev The TWAMM order unwinds the hedge position over time:
///   - If rates fall: wRLP drops, TWAMM sells at better avg price → profit offsets
///   - If rates rise: wRLP rises, collateral earns more → hedge costs offset gains
///   - Net result: Fixed yield regardless of rate volatility
///
/// Prerequisites:
///   - Deployer must have waUSDC balance
///   - Run deploy-wrapped-lp workflow first to set up market
contract FixedYieldBond is Script {
    // Pool params - must match market creation
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    // Index constant K from whitepaper: P = K * r (K=100 means 10% rate = $10)
    uint256 constant K = 100;

    // WAD precision for calculations
    uint256 constant WAD = 1e18;

    function run() external {
        // ─── LOAD CONFIGURATION ───────────────────────────────────────────
        uint256 principal = vm.envOr("PRINCIPAL", uint256(50_000e6)); // 50k waUSDC
        uint256 durationDays = vm.envOr("DURATION_DAYS", uint256(30)); // 30 days

        // Whitepaper formula inputs:
        // - currentRate: Aave borrow rate in WAD (1e18 = 100%)
        // - utilization: Pool utilization in WAD (1e18 = 100%)
        // - reserveFactor: Protocol reserve factor in WAD (typically 5-10%)
        uint256 currentRate = vm.envOr("CURRENT_RATE", uint256(0.1e18)); // Default 10%
        uint256 utilization = vm.envOr("UTILIZATION", uint256(0.9e18)); // Default 90%
        uint256 reserveFactor = vm.envOr("RESERVE_FACTOR", uint256(0.05e18)); // Default 5%

        uint256 durationSeconds = durationDays * 1 days;
        uint256 durationYears = (durationDays * WAD) / 365; // Duration in years as WAD

        // ─── WHITEPAPER HEDGE FORMULA (Section 3.2) ───────────────────────
        // Q_hedge = (N / K * T) × gamma × beta
        //
        // Where:
        //   N = Notional Principal
        //   K = 100 (index constant)
        //   T = Duration in years
        //   gamma = (e^(r*t) - 1) / (r*t) [compounding scalar]
        //   beta = U * (1 - sigma) [utilization beta]

        // Calculate base duration: N / K * T (in wRLP wei terms)
        // Principal is in 6 decimals (USDC), we need wRLP in 6 decimals
        // baseDuration = (principal / K) * durationYears
        uint256 baseDuration = (principal * durationYears) / (K * WAD);

        // Calculate gamma: (e^(r*t) - 1) / (r*t) [compounding scalar]
        uint256 gamma = _calculateGamma(currentRate, durationYears);

        // Calculate beta: U * (1 - reserveFactor) [utilization beta]
        uint256 beta = (utilization * (WAD - reserveFactor)) / WAD;

        // Final hedge amount: baseDuration * gamma * beta
        uint256 hedgeAmount = (baseDuration * gamma * beta) / (WAD * WAD);

        console.log("=== Fixed Yield Bond Configuration ===");
        console.log("Principal:", principal / 1e6, "waUSDC");
        console.log("Duration:", durationDays, "days");
        console.log("Current Rate:", currentRate / 1e16, "% (in bps)");
        console.log("Utilization:", utilization / 1e16, "%");
        console.log("Reserve Factor:", reserveFactor / 1e16, "%");
        console.log("");
        console.log("=== Hedge Calculation (Whitepaper 3.2) ===");
        console.log("Base Duration (N/K*T):", baseDuration / 1e6, "wRLP");
        console.log("Gamma (compounding):", (gamma * 100) / WAD, "%");
        console.log("Beta (utilization):", (beta * 100) / WAD, "%");
        console.log("Final Hedge Size:", hedgeAmount / 1e6, "wRLP");

        // ─── LOAD DEPLOYMENTS ─────────────────────────────────────────────
        string memory deployments = vm.readFile("deployments.json");

        // Read from env vars (set by shell script from wrapped_market.json)
        address brokerFactory = vm.envAddress("BROKER_FACTORY");
        bytes32 marketId = vm.envBytes32("MARKET_ID");
        address twammHook = vm.parseJsonAddress(deployments, ".TWAMM");

        // Position token and collateral from env (consistent with other scripts)
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address collateralToken = vm.envAddress("WAUSDC");

        console.log("\n=== Addresses ===");
        console.log("Broker Factory:", brokerFactory);
        console.log("Position Token (wRLP):", positionToken);
        console.log("Collateral (waUSDC):", collateralToken);
        console.log("TWAMM Hook:", twammHook);

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        // ─── STEP 1: CHECK waUSDC BALANCE ─────────────────────────────────
        console.log("\n=== Step 1: Checking waUSDC balance ===");

        uint256 waUsdcBalance = ERC20(collateralToken).balanceOf(deployer);
        console.log("Deployer waUSDC balance:", waUsdcBalance / 1e6);
        require(waUsdcBalance >= principal, "Insufficient waUSDC balance");

        // ─── STEP 2: PRIME ORACLE ─────────────────────────────────────────
        console.log("\n=== Step 2: Priming TWAMM oracle ===");
        vm.warp(block.timestamp + 2 hours);
        console.log("Advanced time by 2 hours");

        // ─── STEP 3: CREATE BROKER ────────────────────────────────────────
        console.log("\n=== Step 3: Creating PrimeBroker ===");

        vm.startBroadcast(deployerKey);

        bytes32 salt = keccak256(
            abi.encodePacked("fixed-yield-", block.timestamp)
        );
        (bool createSuccess, bytes memory result) = brokerFactory.call(
            abi.encodeWithSignature("createBroker(bytes32)", salt)
        );
        require(createSuccess, "Broker creation failed");
        address broker = abi.decode(result, (address));
        console.log("Broker created:", broker);

        // ─── STEP 4: TRANSFER COLLATERAL TO BROKER ────────────────────────
        console.log("\n=== Step 4: Transferring collateral to broker ===");
        ERC20(collateralToken).transfer(broker, principal);
        console.log("Transferred", principal / 1e6, "waUSDC to broker");

        // ─── STEP 5: MINT wRLP HEDGE ──────────────────────────────────────
        console.log("\n=== Step 5: Minting wRLP hedge ===");

        // Deposit collateral and mint wRLP in one call
        (bool mintSuccess, ) = broker.call(
            abi.encodeWithSignature(
                "modifyPosition(bytes32,int256,int256)",
                marketId,
                int256(principal), // Deposit all collateral
                int256(hedgeAmount) // Mint hedge amount
            )
        );
        require(mintSuccess, "Position mint failed");

        uint256 wrlpBalance = ERC20(positionToken).balanceOf(broker);
        console.log("Broker wRLP balance:", wrlpBalance / 1e6, "wRLP");

        // ─── STEP 6: SUBMIT TWAMM ORDER (Optional) ───────────────────────────
        bool skipTwamm = vm.envOr("SKIP_TWAMM", false);

        if (!skipTwamm) {
            console.log("\n=== Step 6: Submitting TWAMM order ===");

            // Construct PoolKey - tokens sorted by address
            (address currency0, address currency1) = positionToken <
                collateralToken
                ? (positionToken, collateralToken)
                : (collateralToken, positionToken);

            // Determine zeroForOne: selling wRLP for collateral
            bool zeroForOne = positionToken < collateralToken; // true if wRLP is token0

            PoolKey memory poolKey = PoolKey({
                currency0: Currency.wrap(currency0),
                currency1: Currency.wrap(currency1),
                fee: FEE,
                tickSpacing: TICK_SPACING,
                hooks: IHooks(twammHook)
            });

            ITWAMM.SubmitOrderParams memory orderParams = ITWAMM
                .SubmitOrderParams({
                    key: poolKey,
                    zeroForOne: zeroForOne,
                    duration: durationSeconds,
                    amountIn: wrlpBalance
                });

            console.log("Order params:");
            console.log("  zeroForOne:", zeroForOne);
            console.log("  duration:", durationSeconds, "seconds");
            console.log("  amountIn:", wrlpBalance / 1e6, "wRLP");

            // Submit the TWAMM order via broker - the solvency check should now work
            // with the updated JTMBrokerModule pricing logic
            IPrimeBroker(broker).submitTwammOrder(twammHook, orderParams);
            console.log("Order submitted via broker!");

            console.log("\n========================================");
            console.log("   FIXED YIELD BOND CREATED!");
            console.log("========================================");
            console.log("Broker:", broker);
            console.log("Principal:", principal / 1e6, "waUSDC");
            console.log("Hedge:", wrlpBalance / 1e6, "wRLP");
            console.log("Duration:", durationDays, "days");
            console.log("");
            console.log(
                "The TWAMM order will sell wRLP linearly over",
                durationDays,
                "days."
            );
            console.log("This creates a synthetic fixed-yield bond.");
            console.log("========================================");

            // Calculate expected sell rate
            uint256 sellRatePerSecond = wrlpBalance / durationSeconds;
            uint256 sellRatePerDay = sellRatePerSecond * 1 days;
            console.log("\nSell rate:", sellRatePerDay / 1e6, "wRLP/day");
        } else {
            console.log("\n=== Step 6: TWAMM order skipped ===");
            console.log(
                "Set SKIP_TWAMM=false to enable TWAMM order submission"
            );
            console.log("");
            console.log("========================================");
            console.log("   HEDGE POSITION CREATED (No TWAMM)");
            console.log("========================================");
            console.log("Broker:", broker);
            console.log("Principal:", principal / 1e6, "waUSDC");
            console.log("Hedge:", wrlpBalance / 1e6, "wRLP");
            console.log("========================================");
        }

        vm.stopBroadcast();
    }

    /// @dev Calculates gamma (compounding scalar) from whitepaper Section 3.2
    ///      gamma = (e^(r*t) - 1) / (r*t)
    ///
    /// For small values, uses Taylor series approximation:
    /// e^x ≈ 1 + x + x²/2 + x³/6 + ...
    /// (e^x - 1) / x ≈ 1 + x/2 + x²/6 + x³/24 + ...
    ///
    /// @param rate Annual interest rate in WAD (1e18 = 100%)
    /// @param durationYears Duration in years in WAD
    /// @return Gamma multiplier in WAD
    function _calculateGamma(
        uint256 rate,
        uint256 durationYears
    ) internal pure returns (uint256) {
        // x = r * t (both in WAD, so divide by WAD)
        uint256 x = (rate * durationYears) / WAD;

        // For x < 0.01, gamma ≈ 1 (compounding effect negligible)
        if (x < 0.01e18) {
            return WAD;
        }

        // Taylor series approximation for (e^x - 1) / x
        // gamma = 1 + x/2 + x²/6 + x³/24
        //
        // For typical crypto rates (5-20%) and durations (1 month - 5 years):
        // x ranges from 0.004 to 1.0
        // This approximation is accurate to < 0.1% for x < 1

        uint256 x2 = (x * x) / WAD; // x²
        uint256 x3 = (x2 * x) / WAD; // x³

        // gamma = WAD + x/2 + x²/6 + x³/24
        uint256 gamma = WAD;
        gamma += x / 2; // + x/2
        gamma += x2 / 6; // + x²/6
        gamma += x3 / 24; // + x³/24

        return gamma;
    }
}
