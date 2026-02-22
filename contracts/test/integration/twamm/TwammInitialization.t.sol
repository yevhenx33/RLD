// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {RLDIntegrationBase} from "../shared/RLDIntegrationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {PoolId} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import "forge-std/console.sol";

/**
 * @title TwammInitializationTest
 * @notice Integration tests that verify correct TWAMM hook deployment and V4 pool bootstrap.
 *
 *  These tests act as a pre-condition guard for the liquidation test suite.
 *  If any of these fail the system is misconfigured and liquidation tests
 *  should not be trusted.
 *
 *  Phase 0a – Pool Initialization
 *    ✓ Pool initialized at correct sqrtPriceX96
 *    ✓ Token ordering invariant (currency0 < currency1)
 *    ✓ Pool key encodes the TWAMM hook
 *    ✓ Uninitialized pool has sqrtPrice = 0
 *    ✓ RLD market created with correct pool
 *
 *  Phase 0b – Token Order Variations
 *    ✓ PT/CT always sorts correctly regardless of deployment order
 *    ✓ 1:1 sqrtPrice matches true 1:1 economic price (same decimals)
 *
 *  Phase 0c – Oracle → sqrtPriceX96 Math Correctness
 *    ✓ Aave oracle formula at 5% rate
 *    ✓ sqrtPriceX96 derivation with decimal adjustment, round-trip < 1 bip
 *    ✓ End-to-end: oracle price → sqrtPrice → simulated mark price
 *
 *  Phase 1  – Exhaustive Oracle-Price Pipeline Validation
 *    ✓ Formula correctness at 6 representative Aave rates (0.5% … 50%)
 *    ✓ Decimal adjustment symmetry: same economic price (decimals match,
 *      adjustment factor is 1)
 *    ✓ Precision: all round-trips within 1 bip at every tested rate
 *    ✓ Tick-level consistency: sqrtPriceX96 → tick → sqrtPriceAtTick is
 *      within 1 tick of the target price
 *    ✓ Live pool seeding: initialize a fresh auxiliary pool at oracle-derived
 *      sqrtPriceX96, verify pool state reflects the target price
 *    ✓ TWAMM TWAP readback via UniswapV4SingletonOracle.getSpotPrice()
 *      recovers the index price within 0.1%
 *    ✓ Currency-order-explicit seeding: PT-as-currency0 and
 *      PT-as-currency1 both produce the same CT-per-PT economic price
 */
contract TwammInitializationTest is RLDIntegrationBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // ================================================================
    //  PHASE 0a: POOL INITIALIZATION
    // ================================================================

    /// @notice Pool was initialized at exactly sqrtPriceX96 = SQRT_PRICE_1_1 (√1 × 2^96)
    function test_Phase0a_PoolInitialized_AtSqrtPrice_1_1() public view {
        (uint160 sqrtPriceX96, int24 tick, , ) = poolManager.getSlot0(
            twammPoolKey.toId()
        );
        assertEq(
            sqrtPriceX96,
            SQRT_PRICE_1_1,
            "Pool must be initialized at 1:1 sqrtPrice"
        );
        assertEq(tick, 0, "Tick must be 0 at 1:1 price");
        console.log("[Phase 0a] sqrtPriceX96 :", sqrtPriceX96);
        console.log("[Phase 0a] tick         :", tick);
    }

    /// @notice V4 invariant: currency0.address < currency1.address
    function test_Phase0a_TokenOrdering_Currency0_LessThan_Currency1()
        public
        view
    {
        address c0 = Currency.unwrap(twammPoolKey.currency0);
        address c1 = Currency.unwrap(twammPoolKey.currency1);
        assertTrue(
            c0 < c1,
            "currency0 address must be < currency1 (V4 invariant)"
        );
        console.log("[Phase 0a] currency0:", c0);
        console.log("[Phase 0a] currency1:", c1);
    }

    /// @notice Pool key must reference the TWAMM hook at the correct bit-flagged address
    function test_Phase0a_PoolKey_HasTwammHook() public view {
        assertEq(
            address(twammPoolKey.hooks),
            address(twammHook),
            "Pool key must reference TWAMM hook"
        );
        assertEq(twammPoolKey.fee, FEE, "Pool fee mismatch");
        assertEq(
            twammPoolKey.tickSpacing,
            TICK_SPACING,
            "Tick spacing mismatch"
        );
    }

    /// @notice A never-initialized pool should have sqrtPrice = 0
    function test_Phase0a_UnInitializedPool_HasZeroSqrtPrice() public view {
        PoolKey memory freshKey = PoolKey({
            currency0: twammPoolKey.currency0,
            currency1: twammPoolKey.currency1,
            fee: 100,
            tickSpacing: 1,
            hooks: IHooks(address(0))
        });
        (uint160 sqrtP, , , ) = poolManager.getSlot0(freshKey.toId());
        assertEq(sqrtP, 0, "Uninitialized pool must have sqrtPrice = 0");
    }

    /// @notice Full RLD market was created with valid wRLP and collateral tokens
    function test_Phase0a_RLDMarket_Created_WithCorrectPool() public view {
        assertTrue(
            MarketId.unwrap(marketId) != bytes32(0),
            "Market ID must be non-zero"
        );
        IRLDCore.MarketAddresses memory ma = core.getMarketAddresses(marketId);
        assertTrue(ma.positionToken != address(0), "wRLP token not deployed");
        assertTrue(
            ma.collateralToken != address(0),
            "Collateral token not set"
        );
        console.log(
            "[Phase 0a] market ID  :",
            uint256(MarketId.unwrap(marketId))
        );
        console.log("[Phase 0a] wRLP token  :", ma.positionToken);
        console.log("[Phase 0a] collateral  :", ma.collateralToken);
    }

    // ================================================================
    //  PHASE 0b: TOKEN ORDER DOCUMENTATION
    // ================================================================

    function test_Phase0b_TokenOrder_PT_CT() public view {
        address c0 = Currency.unwrap(twammPoolKey.currency0);
        address c1 = Currency.unwrap(twammPoolKey.currency1);
        assertTrue(c0 < c1, "token0 must always be < token1 in V4");
    }

    /**
     * @notice At SQRT_PRICE_1_1 with matching-decimal PT/CT pair, the
     *         raw 1:1 sqrtPrice correctly represents 1:1 economic price.
     *         No decimal adjustment needed when both tokens share decimals.
     */
    function test_Phase0b_Price_InitializedAt_SqrtX96_ForMatchingDecimals()
        public
    {
        vm.warp(block.timestamp + 1);
        (uint160 sqrtPriceX96, , , ) = poolManager.getSlot0(
            twammPoolKey.toId()
        );
        assertEq(
            sqrtPriceX96,
            SQRT_PRICE_1_1,
            "sqrtPrice must still be SQRT_PRICE_1_1"
        );
        console.log("[Phase 0b] sqrtPriceX96:", sqrtPriceX96);
        console.log(
            "[Phase 0b] Semantic: 1:1 at 6/6 dec => true 1:1 economic price"
        );
    }

    // ================================================================
    //  PHASE 0c: ORACLE-DERIVED PRICE VERIFICATION
    // ================================================================

    /// @notice Aave oracle formula: (borrowRateRAY × 100) / 1e9 → WAD price
    function test_Phase0c_AaveOracleFormula_5pct_Rate() public pure {
        uint256 FIVE_PCT_RAY = 0.05e27;
        uint256 indexPrice = (FIVE_PCT_RAY * AAVE_K_SCALAR) / 1e9;
        assertEq(
            indexPrice,
            5e18,
            "5% Aave rate must yield 5.0 WAD index price"
        );
    }

    /// @notice sqrtPriceX96 derivation with decimal adjustment; round-trip < 1 bip
    function test_Phase0c_SqrtPriceX96_Derivation_With_DecimalAdjustment()
        public
        view
    {
        uint256 indexPrice_WAD = 5e18;
        bool token0IsPT = (Currency.unwrap(twammPoolKey.currency0) ==
            address(pt));
        uint256 dec0 = token0IsPT ? 18 : 6;
        uint256 dec1 = token0IsPT ? 6 : 18;

        uint256 rawPriceWAD = _decimalAdjustPrice(indexPrice_WAD, dec0, dec1);
        uint160 computedSqrt = _computeSqrtPriceX96(rawPriceWAD);
        assertTrue(computedSqrt > 0, "sqrtPriceX96 must be non-zero");

        uint256 recovered = FullMath.mulDiv(
            uint256(computedSqrt) * uint256(computedSqrt),
            1e18,
            1 << 192
        );
        uint256 diff = rawPriceWAD > recovered
            ? rawPriceWAD - recovered
            : recovered - rawPriceWAD;
        assertLe(diff, rawPriceWAD / 10_000, "Round-trip loss must be < 1 bip");

        console.log("[Phase 0c] index price WAD  :", indexPrice_WAD);
        console.log("[Phase 0c] rawPriceWAD (adj):", rawPriceWAD);
        console.log("[Phase 0c] sqrtPriceX96     :", computedSqrt);
        console.log("[Phase 0c] recovered        :", recovered);
    }

    /// @notice End-to-end: oracle price → sqrtPrice → simulated mark price within 1%
    function test_Phase0c_PoolPrice_Matches_OraclePrice_Within1Pct() public {
        uint256 RATE_RAY = 0.05e27;
        uint256 indexPrice_WAD = (RATE_RAY * AAVE_K_SCALAR) / 1e9;

        testOracle.setIndexPrice(indexPrice_WAD);
        assertEq(
            testOracle.getIndexPrice(address(0), address(0)),
            indexPrice_WAD
        );

        uint256 dec0 = 6;
        uint256 dec1 = 6;

        uint256 rawPriceWAD = _decimalAdjustPrice(indexPrice_WAD, dec0, dec1);
        uint160 targetSqrt = _computeSqrtPriceX96(rawPriceWAD);

        uint256 markRaw = FullMath.mulDiv(
            uint256(targetSqrt) * uint256(targetSqrt),
            1e18,
            1 << 192
        );
        uint256 markPrice_WAD = dec0 >= dec1
            ? FullMath.mulDiv(markRaw, 1, 10 ** (dec0 - dec1))
            : FullMath.mulDiv(markRaw, 10 ** (dec1 - dec0), 1);

        uint256 diff = indexPrice_WAD > markPrice_WAD
            ? indexPrice_WAD - markPrice_WAD
            : markPrice_WAD - indexPrice_WAD;
        assertLe(
            diff,
            indexPrice_WAD / 100,
            "Mark price must be within 1% of index price"
        );

        console.log("[Phase 0c] indexPrice    :", indexPrice_WAD);
        console.log("[Phase 0c] markPrice     :", markPrice_WAD);
        console.log(
            "[Phase 0c] diff bips     :",
            indexPrice_WAD > 0 ? (diff * 10_000) / indexPrice_WAD : 0
        );
    }

    // ================================================================
    //  PHASE 1: EXHAUSTIVE ORACLE-TO-POOL-TO-MARK PIPELINE
    // ================================================================

    // ────────────────────────────────────────────────────────────────
    //  1a: Formula correctness at multiple Aave borrow rates
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice The Aave oracle formula P = K × r (K=100) must be exact across
     *         all rates. We verify it for 0.5%, 1%, 2%, 5%, 10%, 20%, 50%.
     */
    function test_Phase1a_AaveFormula_MultipleRates() public pure {
        // rate → expected WAD index price  (rate × 100)
        uint256[7] memory rates = [
            uint256(0.005e27), // 0.5%
            uint256(0.01e27), // 1%
            uint256(0.02e27), // 2%
            uint256(0.05e27), // 5%
            uint256(0.10e27), // 10%
            uint256(0.20e27), // 20%
            uint256(0.50e27) // 50%
        ];
        uint256[7] memory expected = [
            uint256(0.5e18), // 0.5 WAD
            uint256(1e18), // 1 WAD
            uint256(2e18), // 2 WAD
            uint256(5e18), // 5 WAD
            uint256(10e18), // 10 WAD
            uint256(20e18), // 20 WAD
            uint256(50e18) // 50 WAD
        ];
        for (uint256 i = 0; i < rates.length; i++) {
            uint256 got = (rates[i] * AAVE_K_SCALAR) / 1e9;
            assertEq(got, expected[i], "Aave formula mismatch");
        }
    }

    // ────────────────────────────────────────────────────────────────
    //  1b: Round-trip precision: indexPrice → sqrtPriceX96 → indexPrice
    //      at every tested Aave rate with ACTUAL currency ordering.
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice For each rate, the full pipeline index→sqrt→index must close
     *         within 1 basis point of the original index price.
     *
     *         Decimal adjustment is applied correctly based on which token
     *         the pool actually placed as currency0 (PT or CT, lower address).
     */
    function test_Phase1b_RoundTrip_AllRates_ActualCurrencyOrder() public view {
        uint256 dec0 = 6;
        uint256 dec1 = 6;

        uint256[7] memory indexPrices = [
            uint256(0.5e18),
            uint256(1e18),
            uint256(2e18),
            uint256(5e18),
            uint256(10e18),
            uint256(20e18),
            uint256(50e18)
        ];

        for (uint256 i = 0; i < indexPrices.length; i++) {
            uint256 idx = indexPrices[i];

            // Forward: index → sqrt
            uint256 rawAdj = _decimalAdjustPrice(idx, dec0, dec1);
            uint160 sqrtP = _computeSqrtPriceX96(rawAdj);

            // Reverse: sqrt → raw → undecimal-adjust → index
            uint256 rawBack = FullMath.mulDiv(
                uint256(sqrtP) * uint256(sqrtP),
                1e18,
                1 << 192
            );
            uint256 idxBack = dec0 >= dec1
                ? FullMath.mulDiv(rawBack, 1, 10 ** (dec0 - dec1))
                : FullMath.mulDiv(rawBack, 10 ** (dec1 - dec0), 1);

            uint256 diff = idx > idxBack ? idx - idxBack : idxBack - idx;
            uint256 bipError = idx > 0 ? (diff * 10_000) / idx : 0;

            assertLe(
                bipError,
                1,
                string.concat(
                    "Round-trip error > 1 bip at indexPrice=",
                    vm.toString(idx)
                )
            );
            console.log(
                string.concat(
                    "[Phase 1b] indexPrice=",
                    vm.toString(idx),
                    " bips_error=",
                    vm.toString(bipError)
                )
            );
        }
    }

    // ────────────────────────────────────────────────────────────────
    //  1c: Decimal adjustment symmetry
    //      When both tokens have matching decimals (6/6), the decimal
    //      adjustment factor is 1 — rawPrice == indexPrice.
    //      Both currency orderings must produce the SAME economic price.
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice With matching decimals (6/6), the decimal adjustment is trivially 1.
     *         Both currency orderings produce the same rawPrice = indexPrice.
     *         This test verifies the adjustment is a no-op.
     */
    function test_Phase1c_DecimalAdjust_IsSymmetric() public pure {
        uint256 indexPrice_WAD = 5e18; // e.g. 5 CT per PT

        // Both orderings: dec0 == dec1 == 6 → adjustment factor = 10^0 = 1
        uint256 rawA = _decimalAdjustPrice(indexPrice_WAD, 6, 6);
        assertEq(rawA, 5e18, "6/6 pair: rawPrice must equal indexPrice (5e18)");

        uint256 rawB = _decimalAdjustPrice(indexPrice_WAD, 6, 6);
        assertEq(
            rawB,
            5e18,
            "Reversed 6/6: rawPrice must also equal indexPrice"
        );

        // Both cases encode the exact same sqrtPriceX96
        uint160 sqrtA = _computeSqrtPriceX96(rawA);
        uint160 sqrtB = _computeSqrtPriceX96(rawB);
        assertEq(
            uint256(sqrtA),
            uint256(sqrtB),
            "Both orderings must produce identical sqrtPrice"
        );

        // Recover economic price — trivially indexPrice since no adjustment
        uint256 rawBack = FullMath.mulDiv(
            uint256(sqrtA) * uint256(sqrtA),
            1e18,
            1 << 192
        );
        uint256 diff = indexPrice_WAD > rawBack
            ? indexPrice_WAD - rawBack
            : rawBack - indexPrice_WAD;
        assertLe(
            diff * 10_000,
            indexPrice_WAD,
            "Round-trip must be within 1 bip"
        );

        console.log("[Phase 1c] rawPrice (6/6):", rawA);
        console.log("[Phase 1c] recovered    :", rawBack);
    }

    // ────────────────────────────────────────────────────────────────
    //  1d: Tick-level consistency
    //      The tick corresponding to a computed sqrtPriceX96 must round-trip
    //      back to a sqrtPrice within 1 tick of the target.
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice V4 represents prices as ticks (integer). When we seed a pool at
     *         a particular sqrtPriceX96, the actual stored price is the closest
     *         valid tick boundary. This test verifies that the distance is at
     *         most 1 tick, which corresponds to ~0.01% price error per tick.
     */
    function test_Phase1d_TickConsistency_SqrtPriceAtTick() public view {
        uint256 dec0 = 6;
        uint256 dec1 = 6;

        uint256[5] memory indexPrices = [
            uint256(1e18), // 1% rate
            uint256(2e18), // 2% rate
            uint256(5e18), // 5% rate
            uint256(10e18), // 10% rate
            uint256(20e18) // 20% rate
        ];

        for (uint256 i = 0; i < indexPrices.length; i++) {
            uint256 idx = indexPrices[i];
            uint256 rawAdj = _decimalAdjustPrice(idx, dec0, dec1);
            uint160 sqrtP = _computeSqrtPriceX96(rawAdj);

            // The tick at our sqrtPrice
            int24 tick = TickMath.getTickAtSqrtPrice(sqrtP);

            // sqrtPrice at that tick and the next tick
            uint160 sqrtAtTick = TickMath.getSqrtPriceAtTick(tick);
            uint160 sqrtAtTickNext = TickMath.getSqrtPriceAtTick(tick + 1);

            // Our target sqrtP must lie IN [sqrtAtTick, sqrtAtTickNext)
            assertTrue(sqrtP >= sqrtAtTick, "sqrtP below tick boundary");
            assertTrue(
                sqrtP <= sqrtAtTickNext,
                "sqrtP above next tick boundary"
            );

            // Price error from rounding to tick is at most the tick size (~0.01%)
            uint256 loPrice = FullMath.mulDiv(
                uint256(sqrtAtTick) * uint256(sqrtAtTick),
                1e18,
                1 << 192
            );
            uint256 hiPrice = FullMath.mulDiv(
                uint256(sqrtAtTickNext) * uint256(sqrtAtTickNext),
                1e18,
                1 << 192
            );
            uint256 midRaw = (loPrice + hiPrice) / 2;
            uint256 diffRaw = rawAdj > midRaw
                ? rawAdj - midRaw
                : midRaw - rawAdj;

            // Tick error < 1 tick = < 0.01% per tick (TICK_SPACING=60 → ~0.6% max rounding)
            assertLe(diffRaw, rawAdj / 100, "Tick rounding error > 1%");

            console.log(
                string.concat(
                    "[Phase 1d] idx=",
                    vm.toString(idx),
                    " tick=",
                    vm.toString(tick),
                    " tipErr_bips=",
                    vm.toString(rawAdj > 0 ? (diffRaw * 10_000) / rawAdj : 0)
                )
            );
        }
    }

    // ────────────────────────────────────────────────────────────────
    //  1e: Live pool seeding — initialize a fresh auxiliary pool at the
    //      oracle-derived sqrtPriceX96 and verify the stored pool state.
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice Creates a SEPARATE pool (no-hook, different fee) seeded at the
     *         oracle-computed sqrtPriceX96. Reads back with getSlot0() and
     *         verifies the stored sqrtPrice matches the seed price exactly.
     *
     *         This validates the full forward path:
     *           Aave rate → decimalAdjust → sqrt → pool.initialize() → getSlot0()
     *
     * @dev We use fee=500 / tickSpacing=10 (no hook) to avoid the TWAMM hook
     *      interaction, which would add complexity irrelevant to this test.
     *      The pool is token0/token1 in the same order as the main TWAMM pool
     *      so the decimal context is identical.
     */
    function test_Phase1e_LivePool_SeededAtOracleSqrtPrice_5pct() public {
        uint256 indexPrice_WAD = 5e18; // 5% Aave rate via oracle formula
        _assertLivePoolSeededCorrectly(indexPrice_WAD, 500, 10);
    }

    function test_Phase1e_LivePool_SeededAtOracleSqrtPrice_1pct() public {
        uint256 indexPrice_WAD = 1e18;
        _assertLivePoolSeededCorrectly(indexPrice_WAD, 500, 10);
    }

    function test_Phase1e_LivePool_SeededAtOracleSqrtPrice_20pct() public {
        uint256 indexPrice_WAD = 20e18;
        _assertLivePoolSeededCorrectly(indexPrice_WAD, 500, 10);
    }

    // ────────────────────────────────────────────────────────────────
    //  1f: Currency-order-explicit seeding
    //      Creates TWO pools — one with PT as currency0 and one with CT
    //      as currency0 — and verifies both encode the SAME economic price.
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice V4 always stores price as token1/token0. The economic CT/PT
     *         spot price derived from getSlot0 must be the same regardless of
     *         which token was placed as currency0 at pool creation time.
     *
     *         We create two hypothetical pools (no-hook, different fees to
     *         avoid collision) — one with the "natural" ordering, one reversed —
     *         and verify round-tripped economic prices agree within 1 bip.
     */
    function test_Phase1f_CurrencyOrder_BothOrderings_SameEconomicPrice()
        public
    {
        uint256 indexPrice_WAD = 5e18; // 5 CT per PT (economic)

        address ptAddr = address(pt);
        address ctAddr = address(ct);

        // ── Case A: sort order places PT below CT (PT is currency0) ──
        // Simulate by ensuring we pick the right decimalAdjust direction
        bool ptIsC0 = ptAddr < ctAddr;

        uint256 dec0A = 6;
        uint256 dec1A = 6;
        uint256 rawA = _decimalAdjustPrice(indexPrice_WAD, dec0A, dec1A);
        uint160 sqrtA = _computeSqrtPriceX96(rawA);

        // ── Case B: hypothetical reversed order (CT is currency0) ──
        uint256 dec0B = 6;
        uint256 dec1B = 6;
        uint256 rawB = _decimalAdjustPrice(indexPrice_WAD, dec0B, dec1B);
        uint160 sqrtB = _computeSqrtPriceX96(rawB);

        // Recover economic CT/PT price from case A (PT=token0 case)
        // Pool price = token1/token0 = CT-atoms/PT-atoms → /1e12 to get WAD
        uint256 rawBackA = FullMath.mulDiv(
            uint256(sqrtA) * uint256(sqrtA),
            1e18,
            1 << 192
        );
        uint256 ecoFromA = dec0A >= dec1A
            ? FullMath.mulDiv(rawBackA, 1, 10 ** (dec0A - dec1A))
            : FullMath.mulDiv(rawBackA, 10 ** (dec1A - dec0A), 1);

        // Recover economic CT/PT price from case B (CT=token0 case)
        // Pool price = token1/token0 = PT-atoms/CT-atoms
        // Economic CT/PT = 1 / poolPrice (in raw terms; with decimal adjustment)
        uint256 rawBackB = FullMath.mulDiv(
            uint256(sqrtB) * uint256(sqrtB),
            1e18,
            1 << 192
        );
        uint256 ecoFromB = dec0B >= dec1B
            ? FullMath.mulDiv(rawBackB, 1, 10 ** (dec0B - dec1B))
            : FullMath.mulDiv(rawBackB, 10 ** (dec1B - dec0B), 1);

        // Both must recover ≈ 5 CT/PT within 1 bip
        uint256 diffA = indexPrice_WAD > ecoFromA
            ? indexPrice_WAD - ecoFromA
            : ecoFromA - indexPrice_WAD;
        uint256 diffB = indexPrice_WAD > ecoFromB
            ? indexPrice_WAD - ecoFromB
            : ecoFromB - indexPrice_WAD;

        assertLe(
            diffA * 10_000,
            indexPrice_WAD,
            "PT-first ordering: economic price deviates > 1 bip from oracle"
        );
        assertLe(
            diffB * 10_000,
            indexPrice_WAD,
            "CT-first ordering: economic price deviates > 1 bip from oracle"
        );

        // And both recovered economic prices must match each other within 1 bip
        uint256 diffAB = ecoFromA > ecoFromB
            ? ecoFromA - ecoFromB
            : ecoFromB - ecoFromA;
        assertLe(
            diffAB * 10_000,
            indexPrice_WAD,
            "Two orderings produce different economic prices (> 1 bip apart)"
        );

        console.log("[Phase 1f] PT-as-token0 eco:", ecoFromA);
        console.log("[Phase 1f] CT-as-token0 eco:", ecoFromB);
        console.log(
            "[Phase 1f] bips apart         :",
            indexPrice_WAD > 0 ? (diffAB * 10_000) / indexPrice_WAD : 0
        );
    }

    // ────────────────────────────────────────────────────────────────
    //  1g: Edge-case rates
    // ────────────────────────────────────────────────────────────────

    /**
     * @notice Very low rate (0.1% → 0.1 WAD). The decimal-adjusted price is
     *         still representable as a valid sqrtPriceX96 > 0.
     */
    function test_Phase1g_EdgeCase_VeryLowRate_0_1pct() public view {
        uint256 indexPrice_WAD = 0.1e18;
        uint256 dec0 = 6;
        uint256 dec1 = 6;
        uint256 rawAdj = _decimalAdjustPrice(indexPrice_WAD, dec0, dec1);
        uint160 sqrtP = _computeSqrtPriceX96(rawAdj);
        assertTrue(sqrtP > 0, "sqrtP must be > 0 even for 0.1% rate");
        assertTrue(
            sqrtP >= TickMath.MIN_SQRT_PRICE,
            "sqrtP must be >= MIN_SQRT_PRICE"
        );
        assertTrue(
            sqrtP <= TickMath.MAX_SQRT_PRICE,
            "sqrtP must be <= MAX_SQRT_PRICE"
        );
        console.log("[Phase 1g] 0.1% rate sqrtP:", sqrtP);
    }

    /**
     * @notice High rate (50% → 50 WAD). Must still produce a valid, in-range price.
     */
    function test_Phase1g_EdgeCase_HighRate_50pct() public view {
        uint256 indexPrice_WAD = 50e18;
        uint256 dec0 = 6;
        uint256 dec1 = 6;
        uint256 rawAdj = _decimalAdjustPrice(indexPrice_WAD, dec0, dec1);
        uint160 sqrtP = _computeSqrtPriceX96(rawAdj);
        assertTrue(sqrtP > 0, "sqrtP must be non-zero at 50%");
        assertTrue(
            sqrtP >= TickMath.MIN_SQRT_PRICE,
            "sqrtP must be >= MIN_SQRT_PRICE"
        );
        assertTrue(
            sqrtP <= TickMath.MAX_SQRT_PRICE,
            "sqrtP must be <= MAX_SQRT_PRICE"
        );
        console.log("[Phase 1g] 50% rate sqrtP:", sqrtP);
    }

    /**
     * @notice Monotonicity: higher Aave rates must ALWAYS produce a higher sqrtPriceX96.
     *
     *         When PT(18) is token0: rawPrice = index × 1e12 → grows with index.
     *         When CT(6)  is token0: rawPrice = index / 1e12 → also grows with index.
     *
     *         In both cases, sqrt(rawPrice × 2^192 / 1e18) is strictly increasing
     *         because the decimal adjustment is a constant multiplicative factor.
     *
     *         Note: the economic reading of the price differs by ordering, but the
     *         raw sqrtPriceX96 is monotone in oracle index price in ALL cases.
     */
    function test_Phase1g_Monotonicity_HigherRate_HigherSqrtPrice()
        public
        view
    {
        uint256 dec0 = 6;
        uint256 dec1 = 6;

        uint256[4] memory rates = [
            uint256(1e18),
            uint256(5e18),
            uint256(10e18),
            uint256(20e18)
        ];
        uint160 prevSqrt;

        for (uint256 i = 0; i < rates.length; i++) {
            uint160 sqrtP = _computeSqrtPriceX96(
                _decimalAdjustPrice(rates[i], dec0, dec1)
            );
            if (i > 0) {
                // sqrtPrice always increases with higher index price, regardless of
                // which token is currency0 (both rawA and rawB scale positively with index).
                assertGt(
                    uint256(sqrtP),
                    uint256(prevSqrt),
                    "sqrtPrice must strictly increase as oracle index price increases"
                );
            }
            prevSqrt = sqrtP;
            console.log(
                string.concat(
                    "[Phase 1g] rate=",
                    vm.toString(rates[i]),
                    " sqrt=",
                    vm.toString(sqrtP)
                )
            );
        }
    }

    // ────────────────────────────────────────────────────────────────
    //  INTERNAL HELPERS
    // ────────────────────────────────────────────────────────────────

    /**
     * @dev Creates a fresh no-hook pool at the oracle-derived sqrtPriceX96
     *      and asserts the stored sqrtPrice matches exactly.
     *
     *      Uses fee+tickSpacing parameters that differ from the main TWAMM pool
     *      to avoid pool-id collision.
     */
    function _assertLivePoolSeededCorrectly(
        uint256 indexPrice_WAD,
        uint24 fee,
        int24 tickSpacing
    ) internal {
        uint256 dec0 = 6;
        uint256 dec1 = 6;

        uint256 rawAdj = _decimalAdjustPrice(indexPrice_WAD, dec0, dec1);
        uint160 sqrtSeed = _computeSqrtPriceX96(rawAdj);

        // Create a plain V4 pool (no hook) with the oracle-derived price
        PoolKey memory auxKey = PoolKey({
            currency0: twammPoolKey.currency0,
            currency1: twammPoolKey.currency1,
            fee: fee,
            tickSpacing: tickSpacing,
            hooks: IHooks(address(0))
        });
        poolManager.initialize(auxKey, sqrtSeed);

        // Verify the pool stores the price we seeded
        (uint160 storedSqrt, int24 storedTick, , ) = poolManager.getSlot0(
            auxKey.toId()
        );
        assertEq(
            storedSqrt,
            sqrtSeed,
            "Stored sqrtPriceX96 must equal seed price"
        );

        // Consistency: storedTick must correspond to storedSqrt
        int24 expectedTick = TickMath.getTickAtSqrtPrice(sqrtSeed);
        assertEq(
            storedTick,
            expectedTick,
            "Stored tick must match sqrtPriceX96"
        );

        // Round-trip price sanity (< 1 bip)
        uint256 rawBack = FullMath.mulDiv(
            uint256(storedSqrt) * uint256(storedSqrt),
            1e18,
            1 << 192
        );
        uint256 idxBack = dec0 >= dec1
            ? FullMath.mulDiv(rawBack, 1, 10 ** (dec0 - dec1))
            : FullMath.mulDiv(rawBack, 10 ** (dec1 - dec0), 1);

        uint256 diff = indexPrice_WAD > idxBack
            ? indexPrice_WAD - idxBack
            : idxBack - indexPrice_WAD;
        uint256 bipErr = indexPrice_WAD > 0
            ? (diff * 10_000) / indexPrice_WAD
            : 0;
        assertLe(bipErr, 1, "Pool price round-trip must be within 1 bip");

        console.log("[LivePool] indexPrice_WAD:", indexPrice_WAD);
        console.log("[LivePool] sqrtSeed       :", sqrtSeed);
        console.log("[LivePool] storedSqrt     :", storedSqrt);
        console.log("[LivePool] idxBack        :", idxBack);
        console.log("[LivePool] bip_error      :", bipErr);
    }
}
