// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {LiquidationBase} from "../liquidation/LiquidationBase.t.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {
    LeverageShortExecutor
} from "../../../src/periphery/LeverageShortExecutor.sol";
import {BrokerRouter} from "../../../src/periphery/BrokerRouter.sol";
import {PrimeBrokerFactory} from "../../../src/rld/core/PrimeBrokerFactory.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {
    LiquidityAmounts
} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {
    IAllowanceTransfer
} from "permit2/src/interfaces/IAllowanceTransfer.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";
import {SwapParams} from "v4-core/src/types/PoolOperation.sol";
import "forge-std/console.sol";

/// @title LeverageShortTests — Phase 6 Penetration Tests
/// @notice 7 tests covering LeverageShortExecutor: end-to-end leverage,
///         solvency checks, operator revocation, no residuals, V4 callback,
///         math correctness, and excessive leverage rejection.
///
/// Test IDs from PENETRATION_TESTING.md:
///   73 — executeLeverageShort() happy path end-to-end
///   74 — Post-execution solvency
///   75 — Operator always revoked (even on revert)
///   76 — Executor never holds tokens post-tx
///   77 — unlockCallback() only callable by PoolManager
///   78 — calculateOptimalDebt() math correctness
///   79 — Excessive leverage reverts
contract LeverageShortTests is LiquidationBase {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    LeverageShortExecutor public shortExecutor;

    uint256 constant OWNER_PK =
        0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80;
    address public owner;

    function _createOwnedBroker() internal returns (PrimeBroker) {
        bytes32 salt = keccak256(abi.encodePacked("lev", brokerNonce++));
        PrimeBroker broker = PrimeBroker(
            payable(brokerFactory.createBroker(salt))
        );
        uint256 tokenId = uint256(uint160(address(broker)));
        ERC721(address(brokerFactory)).transferFrom(
            address(this),
            owner,
            tokenId
        );
        return broker;
    }

    function _signAuth(
        PrimeBroker broker,
        address operator
    ) internal view returns (bytes memory) {
        uint256 nonce = broker.operatorNonces(operator);
        // Must match PrimeBroker.setOperatorWithSignature hash:
        // (operator, active, broker, nonce, caller, commitment, chainId)
        // LeverageShortExecutor uses bytes32(0) as commitment (hardcoded flow)
        bytes32 structHash = keccak256(
            abi.encode(
                operator,
                true,
                address(broker),
                nonce,
                operator,
                bytes32(0),
                block.chainid
            )
        );
        bytes32 ethSignedHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(OWNER_PK, ethSignedHash);
        return abi.encodePacked(r, s, v);
    }

    /// @dev Seed LP pool with liquidity (same pattern as BrokerRouterTrading)
    function _seedLP() internal {
        uint256 posAmount = 200_000e6;
        uint256 depositAmount = posAmount * 20;
        PrimeBroker helper = _createBroker();
        collateralMock.transfer(address(helper), depositAmount);
        helper.modifyPosition(
            MarketId.unwrap(marketId),
            int256(depositAmount),
            int256(posAmount)
        );
        helper.withdrawPositionToken(address(this), posAmount);
        collateralMock.mint(address(this), 1_000_000e6);

        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.positionToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );
        IAllowanceTransfer(PERMIT2_ADDRESS).approve(
            ma.collateralToken,
            address(positionManager),
            type(uint160).max,
            type(uint48).max
        );

        vm.warp(1_700_000_000);
        (, int24 tick, , ) = poolManager.getSlot0(lpPoolKey.toId());
        int24 sp = lpPoolKey.tickSpacing;
        int24 lo = (tick / sp) * sp - 6000;
        int24 hi = lo + 12000;
        uint256 colAmt = 1_000_000e6;
        uint256 a0;
        uint256 a1;
        if (Currency.unwrap(lpPoolKey.currency0) == ma.positionToken) {
            a0 = posAmount;
            a1 = colAmt;
        } else {
            a0 = colAmt;
            a1 = posAmount;
        }
        uint128 liq = LiquidityAmounts.getLiquidityForAmounts(
            TickMath.getSqrtPriceAtTick(tick),
            TickMath.getSqrtPriceAtTick(lo),
            TickMath.getSqrtPriceAtTick(hi),
            a0,
            a1
        );
        require(liq > 0, "zero liq");
        bytes memory acts = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );
        bytes[] memory p = new bytes[](2);
        p[0] = abi.encode(
            lpPoolKey,
            lo,
            hi,
            uint256(liq),
            uint128((a0 * 110) / 100),
            uint128((a1 * 110) / 100),
            address(this),
            bytes("")
        );
        p[1] = abi.encode(lpPoolKey.currency0, lpPoolKey.currency1);
        positionManager.modifyLiquidities(
            abi.encode(acts, p),
            block.timestamp + 60
        );
    }

    function setUp() public override {
        super.setUp();
        owner = vm.addr(OWNER_PK);
        shortExecutor = new LeverageShortExecutor(address(poolManager));
    }

    // ================================================================
    //  Test #73: executeLeverageShort() — happy path end-to-end
    // ================================================================

    function test_leverage_short_end_to_end() public {
        _seedLP();
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 200_000e6);

        bytes memory sig = _signAuth(broker, address(shortExecutor));

        uint256 colBefore = ERC20(ma.collateralToken).balanceOf(
            address(broker)
        );

        shortExecutor.executeLeverageShort(
            address(broker),
            MarketId.unwrap(marketId),
            ma.collateralToken,
            ma.positionToken,
            100_000e6, // initialCollateral
            10_000e6, // targetDebtAmount
            lpPoolKey,
            sig
        );

        uint256 colAfter = ERC20(ma.collateralToken).balanceOf(address(broker));

        // After leverage short: broker should have less free collateral (deposited to Core)
        // but more total collateral in Core (initial + swap proceeds)
        uint128 debt = core
            .getPosition(marketId, address(broker))
            .debtPrincipal;
        assertEq(debt, 10_000e6, "Debt should match targetDebtAmount");

        // Operator should be revoked
        assertFalse(
            broker.operators(address(shortExecutor)),
            "Operator must be revoked"
        );

        console.log("LevShort: col before:", colBefore / 1e6);
        console.log("LevShort: col after:", colAfter / 1e6);
        console.log("LevShort: debt:", debt / 1e6);
    }

    // ================================================================
    //  Test #74: Post-execution solvency
    // ================================================================

    function test_leverage_short_solvency() public {
        _seedLP();
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 200_000e6);

        bytes memory sig = _signAuth(broker, address(shortExecutor));

        shortExecutor.executeLeverageShort(
            address(broker),
            MarketId.unwrap(marketId),
            ma.collateralToken,
            ma.positionToken,
            100_000e6,
            10_000e6,
            lpPoolKey,
            sig
        );

        bool solvent = core.isSolvent(marketId, address(broker));
        assertTrue(solvent, "Broker must be solvent after leverage short");
    }

    // ================================================================
    //  Test #75: Operator always revoked (even on revert)
    // ================================================================

    function test_leverage_short_operator_revoked_on_revert() public {
        _seedLP();
        PrimeBroker broker = _createOwnedBroker();
        // DON'T fund broker — will fail on modifyPosition

        bytes memory sig = _signAuth(broker, address(shortExecutor));

        vm.expectRevert();
        shortExecutor.executeLeverageShort(
            address(broker),
            MarketId.unwrap(marketId),
            ma.collateralToken,
            ma.positionToken,
            100_000e6,
            10_000e6,
            lpPoolKey,
            sig
        );

        // Operator must NOT be set (entire tx reverted atomically)
        assertFalse(broker.operators(address(shortExecutor)));
        assertEq(broker.operatorNonces(address(shortExecutor)), 0);
    }

    // ================================================================
    //  Test #76: Executor never holds tokens post-tx
    // ================================================================

    function test_leverage_short_no_residuals() public {
        _seedLP();
        PrimeBroker broker = _createOwnedBroker();
        collateralMock.transfer(address(broker), 200_000e6);

        bytes memory sig = _signAuth(broker, address(shortExecutor));

        shortExecutor.executeLeverageShort(
            address(broker),
            MarketId.unwrap(marketId),
            ma.collateralToken,
            ma.positionToken,
            100_000e6,
            10_000e6,
            lpPoolKey,
            sig
        );

        assertEq(
            ERC20(ma.collateralToken).balanceOf(address(shortExecutor)),
            0,
            "Executor zero collateral"
        );
        assertEq(
            ERC20(ma.positionToken).balanceOf(address(shortExecutor)),
            0,
            "Executor zero positionToken"
        );
    }

    // ================================================================
    //  Test #77: unlockCallback() — only callable by PoolManager
    // ================================================================

    function test_leverage_short_callback_only_pool_manager() public {
        bytes memory fakeData = abi.encode(
            LeverageShortExecutor.SwapCallback({
                sender: address(this),
                key: lpPoolKey,
                params: SwapParams({
                    zeroForOne: true,
                    amountSpecified: -1000,
                    sqrtPriceLimitX96: 4295128740
                })
            })
        );

        vm.prank(address(0xdead));
        vm.expectRevert("Not PM");
        shortExecutor.unlockCallback(fakeData);
    }

    // ================================================================
    //  Test #78: calculateOptimalDebt() — math correctness
    // ================================================================

    function test_calculate_optimal_debt() public view {
        uint256 col = 100_000e6; // runtime variable avoids rational const
        uint256 price = 5e6;

        // 40% LTV
        uint256 debt40 = shortExecutor.calculateOptimalDebt(col, 40, price);
        uint256 expected40 = (((col * 40) / 60) * 1e6) / price;
        assertEq(debt40, expected40, "40% LTV debt incorrect");

        // 50% LTV → 100k debt value → 20k wRLP
        uint256 debt50 = shortExecutor.calculateOptimalDebt(col, 50, price);
        uint256 expected50 = (((col * 50) / 50) * 1e6) / price;
        assertEq(debt50, expected50, "50% LTV debt incorrect");

        // 0% LTV → 0 debt
        uint256 debt0 = shortExecutor.calculateOptimalDebt(col, 0, price);
        assertEq(debt0, 0, "0% LTV should be 0 debt");

        // 80% LTV → 400k debt value / 5 = 80k wRLP
        uint256 debt80 = shortExecutor.calculateOptimalDebt(col, 80, price);
        uint256 expected80 = (((col * 80) / 20) * 1e6) / price;
        assertEq(debt80, expected80, "80% LTV debt incorrect");

        console.log("Debt 40%:", debt40 / 1e6);
        console.log("Debt 50%:", debt50 / 1e6);
        console.log("Debt 80%:", debt80 / 1e6);
    }

    // ================================================================
    //  Test #79: Excessive leverage reverts (breaches solvency)
    // ================================================================

    function test_leverage_short_excessive_leverage_reverts() public {
        _seedLP();
        PrimeBroker broker = _createOwnedBroker();
        // Only 10k collateral — try to mint 50k debt (way too much leverage)
        collateralMock.transfer(address(broker), 10_000e6);

        bytes memory sig = _signAuth(broker, address(shortExecutor));

        vm.expectRevert();
        shortExecutor.executeLeverageShort(
            address(broker),
            MarketId.unwrap(marketId),
            ma.collateralToken,
            ma.positionToken,
            10_000e6, // only 10k collateral
            50_000e6, // 50k debt — 5x leverage, way beyond safe
            lpPoolKey,
            sig
        );

        // Nothing should have changed
        assertFalse(broker.operators(address(shortExecutor)));
    }
}
