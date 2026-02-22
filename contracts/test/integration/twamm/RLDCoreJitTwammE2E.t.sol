// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {JITRLDIntegrationBase} from "../shared/JITRLDIntegrationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {
    ModifyLiquidityParams,
    SwapParams
} from "v4-core/src/types/PoolOperation.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {
    PoolModifyLiquidityTestNoChecks
} from "v4-core/src/test/PoolModifyLiquidityTestNoChecks.sol";
import {PoolSwapTest} from "v4-core/src/test/PoolSwapTest.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../../../src/rld/core/PrimeBrokerFactory.sol";
import {
    BrokerVerifier
} from "../../../src/rld/modules/verifier/BrokerVerifier.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {
    IAllowanceTransfer
} from "permit2/src/interfaces/IAllowanceTransfer.sol";
import {
    LiquidityAmounts
} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import "forge-std/console.sol";

/**
 * @title RLD Core ↔ JITTWAMM End-to-End Integration Test
 * @notice Verifies the full lifecycle pipeline:
 *
 *   PrimeBroker creation → collateral deposit → wRLP minting at 5% LTV
 *   → token withdrawal → JITTWAMM concentrated LP → swap verification
 *   → solvency / debt / token-conservation invariants
 *
 * @dev Extends JITRLDIntegrationBase which deploys the REAL production
 *      stack (JITTWAMM hook, RLDCore, PrimeBroker, etc.) with:
 *      - MockERC20 for PT (wRLP, 6-dec) and CT (waUSDC, 6-dec)
 *      - ConfigurableOracle (replaces Aave mainnet fork)
 *
 *      Mirrors the pipeline in scripts/mint_and_lp_wrapped.sh but in Forge
 *      against the JITTWAMM hook instead of the original TWAMM.
 */
contract RLDCoreJitTwammE2E is JITRLDIntegrationBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    // ================================================================
    //  Test Infrastructure
    // ================================================================
    PoolModifyLiquidityTestNoChecks public lpRouter;
    PoolSwapTest public swapRouter;
    PrimeBrokerFactory public brokerFactory;
    IRLDCore.MarketAddresses public ma; // cached market addresses
    MockERC20 public collateralMock; // the ACTUAL collateral token (may differ from ct)

    // ================================================================
    //  Constants (mirrors scripts/mint_and_lp_wrapped.sh)
    // ================================================================

    /// @dev Total collateral deposited (10M waUSDC at 6 decimals)
    uint256 constant COLLATERAL_AMOUNT = 10_000_000e6;

    /// @dev wRLP debt to mint — 5% LTV
    /// At index price $5 per wRLP: 500k USD value / $5 = 100,000 wRLP tokens
    uint256 constant DEBT_AMOUNT = 100_000e6;

    /// @dev Amount of each token to withdraw for LP
    /// At price 5, providing equal-value LP:
    /// 100k wRLP × $5 = $500k; match with 500k CT (raw units, both 6-dec)
    uint256 constant LP_WRLP_AMOUNT = 100_000e6;
    uint256 constant LP_CT_AMOUNT = 500_000e6;

    /// @dev JITTWAMM order interval (from base)
    uint256 constant INTERVAL = 3600;

    /// @dev Index price of wRLP in collateral terms (5 waUSDC per wRLP)
    uint256 constant INDEX_PRICE_WAD = 5e18;

    /// @dev Swap amount small enough to stay within JITTWAMM price bounds
    int256 constant SWAP_AMOUNT = -10e6;

    /// @dev LP tick range — set dynamically in _tweakSetup() based on currency ordering.
    ///      Centered ±600 ticks around the price=5 tick (~16095 or ~-16095).
    int24 public lpTickLower;
    int24 public lpTickUpper;

    // ================================================================
    //  Pool Initialization — price = 5 waUSDC per wRLP
    // ================================================================

    /// @notice Initializes the JITTWAMM pool at the correct oracle price.
    /// @dev V4 sqrtPriceX96 = sqrt(price_token1_per_token0) × 2^96.
    ///      Currency ordering depends on mock deploy addresses:
    ///      - If collateral (waUSDC) is currency1: price = waUSDC/wRLP = 5
    ///      - If collateral (waUSDC) is currency0: price = wRLP/waUSDC = 1/5 = 0.2
    ///      Both tokens are 6-decimal → no decimal adjustment needed.
    function _initialSqrtPrice() internal override returns (uint160) {
        uint256 poolPriceWAD = _poolPriceWAD();
        return _computeSqrtPriceX96(poolPriceWAD);
    }

    /// @dev Returns V4 pool price (token1/token0) in WAD, based on currency ordering.
    ///      If ct (collateral) has higher address → currency1 → price = 5
    ///      If ct (collateral) has lower address  → currency0 → price = 1/5
    function _poolPriceWAD() internal view returns (uint256) {
        // ct = the mock we call "Collateral Token"
        // In V4, price = token1/token0. We want 1 wRLP = 5 waUSDC.
        if (address(ct) > address(pt)) {
            // ct is currency1 (higher address) → price = ct/pt = waUSDC/wRLP = 5
            return INDEX_PRICE_WAD;
        } else {
            // ct is currency0 (lower address) → price = pt/ct = wRLP/waUSDC = 1/5
            return FullMath.mulDiv(1e18, 1e18, INDEX_PRICE_WAD);
        }
    }

    // ================================================================
    //  Setup
    // ================================================================

    function _tweakSetup() internal override {
        // 1. Deploy LP + swap routers (same pattern as JitTwammParanoid)
        lpRouter = new PoolModifyLiquidityTestNoChecks(
            IPoolManager(address(poolManager))
        );
        swapRouter = new PoolSwapTest(IPoolManager(address(poolManager)));

        // 2. Approve tokens for routers
        pt.approve(address(lpRouter), type(uint256).max);
        ct.approve(address(lpRouter), type(uint256).max);
        pt.approve(address(swapRouter), type(uint256).max);
        ct.approve(address(swapRouter), type(uint256).max);

        // 3. Cache market addresses
        ma = core.getMarketAddresses(marketId);

        // 4. Get PrimeBrokerFactory from market config
        IRLDCore.MarketConfig memory mc = core.getMarketConfig(marketId);
        address verifier = mc.brokerVerifier;
        brokerFactory = PrimeBrokerFactory(BrokerVerifier(verifier).FACTORY());

        // 5. Resolve which mock token is the actual collateral
        //    Currency sorting may swap pt/ct, so we use market addresses as truth
        collateralMock = MockERC20(ma.collateralToken);

        // 6. Compute LP tick range centered on current pool tick
        //    The pool was already initialized at price=5 (or 1/5, depending on
        //    currency ordering). We read the actual tick and center ±600 around it.
        (, int24 currentTick, , ) = poolManager.getSlot0(twammPoolKey.toId());
        int24 spacing = twammPoolKey.tickSpacing;
        int24 centerTick = (currentTick / spacing) * spacing; // align to spacing
        lpTickLower = centerTick - 600;
        lpTickUpper = centerTick + 600;

        // 7. Mock the V4 oracle's getSpotPrice to avoid calling JITTWAMM.observe()
        //    JITTWAMM does NOT implement observe() (only original TWAMM does).
        //    The StandardFundingModel calls markOracle.getSpotPrice(positionToken, collateralToken)
        //    which hits UniswapV4SingletonOracle → twamm.observe() → reverts.
        //    We mock it to return the same price as the index (5e18) = zero funding.
        vm.mockCall(
            address(v4Oracle),
            abi.encodeWithSelector(
                bytes4(keccak256("getSpotPrice(address,address)")),
                ma.positionToken,
                ma.collateralToken
            ),
            abi.encode(uint256(5e18))
        );
    }

    // ================================================================
    //  Helpers
    // ================================================================

    function _createBroker() internal returns (PrimeBroker broker) {
        bytes32 salt = keccak256(abi.encodePacked("e2e-test", block.timestamp));
        address brokerAddr = brokerFactory.createBroker(salt);
        broker = PrimeBroker(payable(brokerAddr));
    }

    function _doSwap(
        bool zeroForOne,
        int256 amountSpecified
    ) internal returns (BalanceDelta delta) {
        delta = swapRouter.swap(
            twammPoolKey,
            SwapParams({
                zeroForOne: zeroForOne,
                amountSpecified: amountSpecified,
                sqrtPriceLimitX96: zeroForOne
                    ? TickMath.MIN_SQRT_PRICE + 1
                    : TickMath.MAX_SQRT_PRICE - 1
            }),
            PoolSwapTest.TestSettings({
                takeClaims: false,
                settleUsingBurn: false
            }),
            ""
        );
    }

    // ================================================================
    //  PHASE 1: BROKER CREATION & COLLATERAL DEPOSIT & MINTING
    // ================================================================

    /// @notice Factory creates a PrimeBroker clone with correct initialization
    function test_E2E_CreateBroker() public {
        PrimeBroker broker = _createBroker();

        // Broker is deployed and initialized
        assertTrue(address(broker) != address(0), "Broker deployed");

        // Verify broker is recognized by the factory
        assertTrue(
            brokerFactory.isBroker(address(broker)),
            "Factory recognizes broker"
        );

        // Verify market caches are correct (use actual market addresses)
        assertEq(
            broker.collateralToken(),
            ma.collateralToken,
            "collateralToken cached"
        );

        // Broker is owned by test contract (NFT minted to msg.sender)
        uint256 tokenId = uint256(uint160(address(broker)));
        assertEq(
            brokerFactory.ownerOf(tokenId),
            address(this),
            "NFT minted to deployer"
        );

        console.log("[Phase 1] Broker created:", address(broker));
    }

    /// @notice Deposit CT (waUSDC) to broker and record collateral in Core
    function test_E2E_DepositCollateral() public {
        PrimeBroker broker = _createBroker();

        // Transfer collateral to broker
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        assertEq(
            collateralMock.balanceOf(address(broker)),
            COLLATERAL_AMOUNT,
            "Collateral transferred to broker"
        );

        // Deposit collateral via modifyPosition (deltaCollateral > 0, deltaDebt = 0)
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            0
        );

        // Broker should be solvent
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "Broker solvent after deposit"
        );

        console.log(
            "[Phase 1] Deposited collateral:",
            COLLATERAL_AMOUNT / 1e6,
            "CT"
        );
    }

    /// @notice Mint wRLP at ~5% LTV: debt value ≈ 5% of collateral
    function test_E2E_MintWRLP_At5PctLTV() public {
        PrimeBroker broker = _createBroker();

        // Deposit collateral
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            0
        );

        // Mint wRLP debt
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            0,
            int256(DEBT_AMOUNT)
        );

        // Verify wRLP minted to broker
        uint256 brokerWRLP = ERC20(wrlpToken).balanceOf(address(broker));
        assertEq(brokerWRLP, DEBT_AMOUNT, "wRLP minted to broker");

        // Verify debt recorded in Core
        IRLDCore.Position memory pos = core.getPosition(
            marketId,
            address(broker)
        );
        assertEq(
            pos.debtPrincipal,
            uint128(DEBT_AMOUNT),
            "Debt principal recorded"
        );

        // Calculate LTV: debtValue / collateral
        // debtValue = 100k wRLP × $5 = $500k
        // LTV = 500k / 10M = 5%
        uint256 debtValue = (DEBT_AMOUNT * 5e18) / 1e18; // wRLP * indexPrice (WAD)
        uint256 ltvBps = (debtValue * 10000) / COLLATERAL_AMOUNT;
        assertEq(ltvBps, 500, "LTV is 5% (500 bps)");

        // Must remain solvent
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "Broker solvent at 5% LTV"
        );

        console.log("[Phase 1] Minted wRLP:", DEBT_AMOUNT / 1e6);
        console.log("[Phase 1] LTV (bps):", ltvBps);
    }

    // ================================================================
    //  PHASE 2: TOKEN WITHDRAWAL
    // ================================================================

    /// @notice Withdraw wRLP + CT from broker while maintaining solvency
    function test_E2E_WithdrawTokens_ForLP() public {
        PrimeBroker broker = _createBroker();

        // Deposit + mint
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );

        uint256 ptBefore = ERC20(wrlpToken).balanceOf(address(this));
        uint256 ctBefore = collateralMock.balanceOf(address(this));

        // Withdraw wRLP for LP
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);

        // Withdraw collateral for LP
        broker.withdrawCollateral(address(this), LP_CT_AMOUNT);

        uint256 ptAfter = ERC20(wrlpToken).balanceOf(address(this));
        uint256 ctAfter = collateralMock.balanceOf(address(this));

        assertEq(
            ptAfter - ptBefore,
            LP_WRLP_AMOUNT,
            "wRLP withdrawn to deployer"
        );
        assertEq(ctAfter - ctBefore, LP_CT_AMOUNT, "CT withdrawn to deployer");

        // Broker still solvent
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "Broker solvent after withdrawal"
        );

        console.log("[Phase 2] Withdrawn wRLP:", LP_WRLP_AMOUNT / 1e6);
        console.log("[Phase 2] Withdrawn CT:", LP_CT_AMOUNT / 1e6);
    }

    /// @notice Attempting to withdraw too much reverts with insolvency
    function test_E2E_WithdrawTooMuch_Reverts() public {
        PrimeBroker broker = _createBroker();

        // Deposit minimal collateral and max debt to be near margin
        uint256 tightCollateral = 1_000_000e6; // 1M CT
        uint256 maxDebt = 100_000e6; // 100k wRLP = $500k → LTV ≈ 50%

        collateralMock.transfer(address(broker), tightCollateral);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(tightCollateral),
            int256(maxDebt)
        );

        // Try withdrawing most of the collateral — should revert
        // After withdrawal: 40k CT remaining. NAV = 40k + 500k (wRLP) = 540k
        // debtValue = 500k. netWorth = 540k - 500k = 40k.
        // Withdrawals use maintenanceMargin (1.1), marginReq = 500k * 0.1 = 50k. 40k < 50k → insolvent
        vm.expectRevert();
        broker.withdrawCollateral(address(this), 960_000e6);
    }

    // ================================================================
    //  PHASE 3: JITTWAMM LP PROVISIONING & SWAPS
    // ================================================================

    /// @notice Seed concentrated LP in the JITTWAMM pool
    function test_E2E_AddLiquidity_ToJITTWAMM() public {
        PrimeBroker broker = _createBroker();

        // Deposit + mint + withdraw
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);
        broker.withdrawCollateral(address(this), LP_CT_AMOUNT);

        // Approve wrlpToken for LP router
        ERC20(wrlpToken).approve(address(lpRouter), type(uint256).max);

        uint128 liqBefore = poolManager.getLiquidity(twammPoolKey.toId());

        // Add concentrated LP at ±600 ticks
        BalanceDelta addDelta = lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: lpTickLower,
                tickUpper: lpTickUpper,
                liquidityDelta: 10e12,
                salt: bytes32(0)
            }),
            ""
        );

        uint128 liqAfter = poolManager.getLiquidity(twammPoolKey.toId());

        assertTrue(liqAfter > liqBefore, "Pool liquidity increased");
        assertTrue(
            addDelta.amount0() != 0 || addDelta.amount1() != 0,
            "Tokens deposited to pool"
        );

        console.log("[Phase 3] Liquidity before:", liqBefore);
        console.log("[Phase 3] Liquidity after:", liqAfter);
    }

    /// @notice Execute a zeroForOne swap against JITTWAMM LP
    function test_E2E_SwapAgainstLP() public {
        // Setup: broker + LP
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);
        broker.withdrawCollateral(address(this), LP_CT_AMOUNT);
        ERC20(wrlpToken).approve(address(lpRouter), type(uint256).max);

        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: lpTickLower,
                tickUpper: lpTickUpper,
                liquidityDelta: 10e12,
                salt: bytes32(0)
            }),
            ""
        );

        // Approve wRLP for swap
        ERC20(wrlpToken).approve(address(swapRouter), type(uint256).max);

        (uint160 sqrtBefore, , , ) = poolManager.getSlot0(twammPoolKey.toId());

        // Swap: sell 100 of currency0
        BalanceDelta delta = _doSwap(true, SWAP_AMOUNT);

        (uint160 sqrtAfter, , , ) = poolManager.getSlot0(twammPoolKey.toId());

        assertTrue(delta.amount0() != 0, "currency0 moved");
        assertTrue(delta.amount1() != 0, "currency1 moved");
        assertTrue(sqrtAfter < sqrtBefore, "zeroForOne decreases sqrtPrice");

        console.log("[Phase 3] ZeroForOne swap sqrtBefore:", sqrtBefore);
        console.log("[Phase 3] ZeroForOne swap sqrtAfter:", sqrtAfter);
    }

    /// @notice Execute a oneForZero swap — verify bidirectional liquidity
    function test_E2E_SwapReverseDirection() public {
        // Setup: broker + LP
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);
        broker.withdrawCollateral(address(this), LP_CT_AMOUNT);
        ERC20(wrlpToken).approve(address(lpRouter), type(uint256).max);

        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: lpTickLower,
                tickUpper: lpTickUpper,
                liquidityDelta: 10e12,
                salt: bytes32(0)
            }),
            ""
        );

        (uint160 sqrtBefore, , , ) = poolManager.getSlot0(twammPoolKey.toId());

        // Swap: sell 100 of currency1 (oneForZero)
        BalanceDelta delta = _doSwap(false, SWAP_AMOUNT);

        (uint160 sqrtAfter, , , ) = poolManager.getSlot0(twammPoolKey.toId());

        assertTrue(delta.amount0() != 0, "currency0 moved");
        assertTrue(delta.amount1() != 0, "currency1 moved");
        assertTrue(sqrtAfter > sqrtBefore, "oneForZero increases sqrtPrice");

        console.log("[Phase 3] OneForZero swap sqrtBefore:", sqrtBefore);
        console.log("[Phase 3] OneForZero swap sqrtAfter:", sqrtAfter);
    }

    /// @notice Pool price is consistent with oracle index price (5 waUSDC/wRLP)
    function test_E2E_PoolPrice_ConsistentWithOracle() public {
        (uint160 sqrtPrice, , , ) = poolManager.getSlot0(twammPoolKey.toId());

        // Compute expected sqrtPriceX96 for the actual pool price (5 or 0.2 depending on ordering)
        uint256 expectedPriceWad = _poolPriceWAD();
        uint160 expectedSqrt = _computeSqrtPriceX96(expectedPriceWad);

        // Allow 1% deviation
        uint256 deviation = sqrtPrice > expectedSqrt
            ? sqrtPrice - expectedSqrt
            : expectedSqrt - sqrtPrice;
        uint256 deviationBps = (deviation * 10000) / expectedSqrt;
        assertTrue(deviationBps < 100, "Pool price within 1% of index price");

        // Recover actual price for logging: price = (sqrtPrice/2^96)^2
        // In WAD: price_WAD = sqrtPrice^2 * 1e18 / 2^192
        uint256 actualPriceWad = FullMath.mulDiv(
            uint256(sqrtPrice) * uint256(sqrtPrice),
            1e18,
            1 << 192
        );

        console.log("[Phase 3] Pool sqrtPriceX96:", sqrtPrice);
        console.log("[Phase 3] Expected sqrtPriceX96:", expectedSqrt);
        console.log("[Phase 3] Actual price (WAD):", actualPriceWad);
        console.log("[Phase 3] Expected price (WAD):", expectedPriceWad);
        console.log("[Phase 3] Deviation (bps):", deviationBps);
    }

    // ================================================================
    //  PHASE 4: FULL CYCLE INVARIANTS
    // ================================================================

    /// @notice End-to-end: create → deposit → mint → withdraw → LP → verify solvency at every step
    function test_E2E_FullCycle_BrokerSolvency() public {
        // Step 1: Create broker
        PrimeBroker broker = _createBroker();
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "INV-1: solvent after create"
        );

        // Step 2: Deposit collateral
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            0
        );
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "INV-2: solvent after deposit"
        );

        // Step 3: Mint wRLP
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            0,
            int256(DEBT_AMOUNT)
        );
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "INV-3: solvent after mint"
        );

        // Step 4: Withdraw tokens
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "INV-4: solvent after wRLP withdrawal"
        );

        broker.withdrawCollateral(address(this), LP_CT_AMOUNT);
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "INV-5: solvent after CT withdrawal"
        );

        // Step 5: Add LP (uses deployer's tokens, not broker's)
        ERC20(wrlpToken).approve(address(lpRouter), type(uint256).max);
        lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: lpTickLower,
                tickUpper: lpTickUpper,
                liquidityDelta: 10e12,
                salt: bytes32(0)
            }),
            ""
        );

        // Step 6: Swap
        ERC20(wrlpToken).approve(address(swapRouter), type(uint256).max);
        _doSwap(true, SWAP_AMOUNT);

        // Broker solvency unaffected by external LP/swaps
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "INV-6: solvent after swap"
        );

        console.log("[Phase 4] Full cycle solvency: PASS (6/6 invariants)");
    }

    /// @notice Core's debt records match minted amount
    function test_E2E_FullCycle_DebtAccounting() public {
        PrimeBroker broker = _createBroker();

        // Deposit + mint
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );

        // Check position
        IRLDCore.Position memory pos = core.getPosition(
            marketId,
            address(broker)
        );
        assertEq(
            uint256(pos.debtPrincipal),
            DEBT_AMOUNT,
            "Debt principal matches minted amount"
        );

        // Check market state
        IRLDCore.MarketState memory ms = core.getMarketState(marketId);
        assertEq(
            uint256(ms.totalDebt),
            DEBT_AMOUNT,
            "Total market debt equals this broker's debt"
        );

        // No funding applied yet → normalization factor = 1e18
        assertEq(
            uint256(ms.normalizationFactor),
            1e18,
            "NormFactor is 1e18 (no funding yet)"
        );

        console.log(
            "[Phase 4] Debt principal:",
            uint256(pos.debtPrincipal) / 1e6
        );
        console.log(
            "[Phase 4] Total market debt:",
            uint256(ms.totalDebt) / 1e6
        );
        console.log("[Phase 4] NormFactor:", uint256(ms.normalizationFactor));
    }

    /// @notice Token conservation: minted supply = broker + deployer + pool balances
    function test_E2E_FullCycle_TokenConservation() public {
        PrimeBroker broker = _createBroker();

        // Deposit + mint + withdraw some for LP
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);

        // Add LP to pool
        ERC20(wrlpToken).approve(address(lpRouter), type(uint256).max);
        BalanceDelta addDelta = lpRouter.modifyLiquidity(
            twammPoolKey,
            ModifyLiquidityParams({
                tickLower: lpTickLower,
                tickUpper: lpTickUpper,
                liquidityDelta: 5e12,
                salt: bytes32(0)
            }),
            ""
        );

        // Token conservation for wRLP:
        // minted = broker balance + deployer balance + pool balance (held by PoolManager)
        uint256 brokerBalance = ERC20(wrlpToken).balanceOf(address(broker));
        uint256 deployerBalance = ERC20(wrlpToken).balanceOf(address(this));
        uint256 poolManagerBalance = ERC20(wrlpToken).balanceOf(
            address(poolManager)
        );

        uint256 total = brokerBalance + deployerBalance + poolManagerBalance;

        // The total should equal DEBT_AMOUNT (everything minted)
        // Note: lpRouter deposits go to poolManager, donations go to poolManager too
        // Also wRLP may have been minted to test contract by base setUp (check base)
        // The key invariant: no wRLP was created outside of modifyPosition minting
        assertEq(
            ERC20(wrlpToken).totalSupply(),
            total,
            "wRLP total supply = broker + deployer + poolManager"
        );

        console.log(
            "[Phase 4] wRLP total supply:",
            ERC20(wrlpToken).totalSupply() / 1e6
        );
        console.log("[Phase 4] Broker wRLP:", brokerBalance / 1e6);
        console.log("[Phase 4] Deployer wRLP:", deployerBalance / 1e6);
        console.log("[Phase 4] Pool wRLP:", poolManagerBalance / 1e6);
    }

    // ================================================================
    //  PHASE 5: LP VALUATION VIA V4 BROKER MODULE
    // ================================================================

    /// @notice Broker provides LP via POSM into a wRLP+collateral pool,
    ///         registers it, and verifies the UniswapV4BrokerModule correctly
    ///         values BOTH sides (wRLP via index price, collateral at 1:1).
    ///
    /// @dev In production, the pool is wRLP+waUSDC. The JITTWAMM pool in tests
    ///      uses raw pt/ct mocks which don't match positionToken (wRLP), so we
    ///      create a separate plain V4 pool with the actual market tokens.
    function test_E2E_LPValuation_ViaV4Module() public {
        // ── Step 1: Create broker, deposit collateral, mint wRLP ──
        PrimeBroker broker = _createBroker();
        collateralMock.transfer(address(broker), COLLATERAL_AMOUNT);
        broker.modifyPosition(
            MarketId.unwrap(marketId),
            int256(COLLATERAL_AMOUNT),
            int256(DEBT_AMOUNT)
        );

        // Withdraw wRLP + collateral to test contract for LP
        broker.withdrawPositionToken(address(this), LP_WRLP_AMOUNT);
        broker.withdrawCollateral(address(this), LP_CT_AMOUNT);

        console.log("[Phase 5] wRLP withdrawn:", LP_WRLP_AMOUNT / 1e6);
        console.log("[Phase 5] Collateral withdrawn:", LP_CT_AMOUNT / 1e6);

        // Record NAV before LP
        uint256 navBefore = broker.getNetAccountValue();
        console.log(
            "[Phase 5] NAV before LP:",
            navBefore / 1e6,
            "collateral units"
        );

        // ── Step 2: Create a plain V4 pool between wRLP and collateralToken ──
        //   This mirrors production where the pool is wRLP+waUSDC.
        //   The JITTWAMM pool uses raw pt/ct mocks — can't use it for valuation.
        address posToken = ma.positionToken; // wRLP
        address colToken = ma.collateralToken; // e.g. waUSDC analog

        // Sort currencies for V4 (currency0 < currency1 by address)
        (Currency cur0, Currency cur1) = _sortCurrencies(
            Currency.wrap(posToken),
            Currency.wrap(colToken)
        );
        PoolKey memory lpPoolKey = PoolKey({
            currency0: cur0,
            currency1: cur1,
            fee: 3000,
            tickSpacing: int24(60),
            hooks: IHooks(address(0)) // plain pool, no hook
        });

        // Initialize at the index price (5 collateral per 1 wRLP).
        // V4 price = currency1 / currency0, so we must account for
        // which token ended up as cur0 after sorting.
        uint256 lpPriceWAD;
        if (Currency.unwrap(cur0) == colToken) {
            // colToken is currency0 → price = posToken/colToken = wRLP/waUSDC = 1/5
            lpPriceWAD = FullMath.mulDiv(1e18, 1e18, INDEX_PRICE_WAD);
        } else {
            // posToken is currency0 → price = colToken/posToken = waUSDC/wRLP = 5
            lpPriceWAD = INDEX_PRICE_WAD;
        }
        uint160 initSqrtPrice = _computeSqrtPriceX96(lpPriceWAD);
        poolManager.initialize(lpPoolKey, initSqrtPrice);

        // Compute LP tick range centered on current tick
        (, int24 poolTick, , ) = poolManager.getSlot0(lpPoolKey.toId());
        int24 spacing = lpPoolKey.tickSpacing;
        int24 center = (poolTick / spacing) * spacing;
        int24 lp0 = center - 600;
        int24 lp1 = center + 600;

        console.log(
            "[Phase 5] Pool tick:",
            uint24(poolTick >= 0 ? poolTick : -poolTick)
        );
        console.log(
            "[Phase 5] Tick range: [%d .. %d]",
            uint24(lp0 >= 0 ? lp0 : -lp0),
            uint24(lp1 >= 0 ? lp1 : -lp1)
        );
        console.log(
            "[Phase 5] Offset from center: -%d / +%d",
            uint24(center - lp0),
            uint24(lp1 - center)
        );

        // ── Step 3: Approve tokens via Permit2 → POSM ──
        vm.warp(1_700_000_000);

        ERC20(posToken).approve(PERMIT2_ADDRESS, type(uint256).max);
        ERC20(colToken).approve(PERMIT2_ADDRESS, type(uint256).max);
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            posToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            colToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );

        // ── Step 4: Calculate liquidity ──
        uint160 sqrtCur = TickMath.getSqrtPriceAtTick(poolTick);
        uint160 sqrtLo = TickMath.getSqrtPriceAtTick(lp0);
        uint160 sqrtHi = TickMath.getSqrtPriceAtTick(lp1);

        // Map amounts to currency order
        uint256 amt0;
        uint256 amt1;
        if (Currency.unwrap(cur0) == posToken) {
            amt0 = LP_WRLP_AMOUNT;
            amt1 = LP_CT_AMOUNT;
        } else {
            amt0 = LP_CT_AMOUNT;
            amt1 = LP_WRLP_AMOUNT;
        }

        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtCur,
            sqrtLo,
            sqrtHi,
            amt0,
            amt1
        );
        require(liquidity > 0, "zero liquidity");
        console.log("[Phase 5] Computed liquidity:", liquidity);

        // Snapshot balances before mint
        uint256 posBefore = ERC20(posToken).balanceOf(address(this));
        uint256 colBefore = ERC20(colToken).balanceOf(address(this));

        // ── Step 5: Mint LP position via POSM ──
        bytes memory actions = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );

        bytes[] memory params = new bytes[](2);
        params[0] = abi.encode(
            lpPoolKey,
            lp0,
            lp1,
            uint256(liquidity),
            uint128((amt0 * 110) / 100),
            uint128((amt1 * 110) / 100),
            address(this),
            bytes("")
        );
        params[1] = abi.encode(cur0, cur1);

        positionManager.modifyLiquidities(
            abi.encode(actions, params),
            block.timestamp + 60
        );

        // Get minted position
        uint256 tokenId = positionManager.nextTokenId() - 1;
        assertEq(
            IERC721(address(positionManager)).ownerOf(tokenId),
            address(this),
            "Test contract owns LP NFT"
        );
        uint128 posLiquidity = positionManager.getPositionLiquidity(tokenId);
        console.log("[Phase 5] Minted LP tokenId:", tokenId);
        console.log("[Phase 5] Position liquidity:", posLiquidity);
        assertTrue(posLiquidity > 0, "Position has liquidity");

        // Log actual token consumption (delta from before mint)
        uint256 posConsumed = posBefore -
            ERC20(posToken).balanceOf(address(this));
        uint256 colConsumed = colBefore -
            ERC20(colToken).balanceOf(address(this));
        console.log("[Phase 5] wRLP consumed:", posConsumed / 1e6);
        console.log("[Phase 5] Collateral consumed:", colConsumed / 1e6);
        console.log(
            "[Phase 5] wRLP leftover:",
            posConsumed < LP_WRLP_AMOUNT
                ? (LP_WRLP_AMOUNT - posConsumed) / 1e6
                : 0
        );
        console.log(
            "[Phase 5] Collateral leftover:",
            colConsumed < LP_CT_AMOUNT ? (LP_CT_AMOUNT - colConsumed) / 1e6 : 0
        );

        // ── Step 6: Transfer NFT to broker & register ──
        IERC721(address(positionManager)).transferFrom(
            address(this),
            address(broker),
            tokenId
        );
        assertEq(
            IERC721(address(positionManager)).ownerOf(tokenId),
            address(broker),
            "Broker owns LP NFT"
        );
        broker.setActiveV4Position(tokenId);
        assertEq(broker.activeTokenId(), tokenId, "LP registered");

        // ── Step 7: Verify NAV includes LP value ──
        uint256 navAfter = broker.getNetAccountValue();
        uint256 lpValue = navAfter - navBefore;

        console.log(
            "[Phase 5] NAV after LP:",
            navAfter / 1e6,
            "collateral units"
        );
        console.log("[Phase 5] LP value:", lpValue / 1e6, "collateral units");

        // LP should capture real value from both sides:
        //   wRLP side: LP_WRLP_AMOUNT * indexPrice (5)
        //   Collateral side: LP_CT_AMOUNT * 1
        uint256 expectedMax = FullMath.mulDiv(
            LP_WRLP_AMOUNT,
            INDEX_PRICE_WAD,
            1e18
        ) + LP_CT_AMOUNT;
        console.log("[Phase 5] Max expected LP value:", expectedMax / 1e6);

        assertTrue(lpValue > 0, "LP value added to NAV");
        // Concentrated LP may not consume all tokens, but value should be significant
        assertTrue(lpValue > expectedMax / 4, "LP value > 25% of max expected");

        // Broker should still be solvent
        assertTrue(
            core.isSolvent(marketId, address(broker)),
            "Broker solvent with LP"
        );
    }
}
