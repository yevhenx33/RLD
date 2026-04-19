// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {TwapEngine} from "../../../src/twamm_v3/TwapEngine.sol";
import {MockERC20} from "../mocks/MockERC20.sol";
import {MockGhostRouterForEngine} from "../mocks/MockGhostRouterForEngine.sol";

contract TwapEngineInvariantHandler is Test {
    uint256 internal constant INTERVAL = 60;
    uint256 internal constant PRICE_SCALE = 1e18;
    uint256 internal constant MAX_TRACKED_ORDERS = 128;
    uint256 internal constant MAX_ORDER_AMOUNT = 50_000e18;
    uint256 internal constant MAX_TAKER_INPUT = 25_000e18;
    uint256 internal constant INITIAL_ACTOR_FUNDS = 2_000_000e18;

    address internal constant ALICE = address(0xA11CE);
    address internal constant BOB = address(0xB0B01);
    address internal constant CAROL = address(0xCA401);
    address internal constant SOLVER = address(0x50BEE);

    struct OrderRef {
        bytes32 marketId;
        bytes32 orderId;
        address owner;
    }

    TwapEngine public immutable engine;
    MockGhostRouterForEngine public immutable router;
    MockERC20 public immutable token0;
    MockERC20 public immutable token1;
    bytes32 public immutable marketA;
    bytes32 public immutable marketB;

    bool public initialized;
    OrderRef[] internal trackedOrders;

    constructor(
        TwapEngine _engine,
        MockGhostRouterForEngine _router,
        MockERC20 _token0,
        MockERC20 _token1,
        bytes32 _marketA,
        bytes32 _marketB
    ) {
        engine = _engine;
        router = _router;
        token0 = _token0;
        token1 = _token1;
        marketA = _marketA;
        marketB = _marketB;
    }

    function initializeAccounts() external {
        if (initialized) return;
        initialized = true;

        address[4] memory users = [ALICE, BOB, CAROL, SOLVER];
        for (uint256 i = 0; i < users.length; ++i) {
            token0.mint(users[i], INITIAL_ACTOR_FUNDS);
            token1.mint(users[i], INITIAL_ACTOR_FUNDS);

            vm.startPrank(users[i]);
            token0.approve(address(router), type(uint256).max);
            token1.approve(address(router), type(uint256).max);
            vm.stopPrank();
        }
    }

    // ─────────────────────────────────────────────────────────────────────────────
    // Stateful actions (targeted by invariant fuzzer)
    // ─────────────────────────────────────────────────────────────────────────────

    function warpTime(uint32 rawDelta) external {
        uint256 delta = _clamp(uint256(rawDelta), 1, 12 hours);
        vm.warp(block.timestamp + delta);
    }

    function tuneSpotPrice(uint8 marketSeed, uint256 rawSpotPrice) external {
        bytes32 marketId = _market(marketSeed);
        uint256 spotPrice = _clamp(rawSpotPrice, 1e12, 1e30);
        router.setSpotPrice(marketId, spotPrice);
    }

    function syncMarket(uint8 marketSeed) external {
        bytes32 marketId = _market(marketSeed);
        vm.prank(address(router));
        engine.syncAndFetchGhost(marketId);
    }

    function submitStream(
        uint8 actorSeed,
        uint8 marketSeed,
        bool zeroForOne,
        uint8 rawDurationIntervals,
        uint96 rawAmount
    ) external {
        if (trackedOrders.length >= MAX_TRACKED_ORDERS) return;

        address actor = _actor(actorSeed);
        bytes32 marketId = _market(marketSeed);
        uint256 durationIntervals = _clamp(uint256(rawDurationIntervals), 1, 24);
        uint256 duration = durationIntervals * INTERVAL;
        uint256 amountIn = _clamp(uint256(rawAmount), duration, MAX_ORDER_AMOUNT);

        MockERC20 sellToken = zeroForOne ? token0 : token1;
        _ensureFunded(sellToken, actor, amountIn);

        vm.prank(actor);
        try engine.submitStream(marketId, zeroForOne, duration, amountIn) returns (bytes32 orderId) {
            trackedOrders.push(OrderRef({marketId: marketId, orderId: orderId, owner: actor}));
        } catch {}
    }

    function claimOrder(uint256 orderSeed) external {
        (bool found, OrderRef memory ref) = _activeOrder(orderSeed);
        if (!found) return;

        vm.prank(ref.owner);
        try engine.claimTokens(ref.marketId, ref.orderId) returns (uint256) {} catch {}
    }

    function cancelOrder(uint256 orderSeed) external {
        (bool found, OrderRef memory ref) = _activeOrder(orderSeed);
        if (!found) return;

        vm.prank(ref.owner);
        try engine.cancelOrder(ref.marketId, ref.orderId) returns (uint256, uint256) {} catch {}
    }

    function clearAuction(uint8 marketSeed, bool zeroForOne, uint96 rawMaxAmount) external {
        bytes32 marketId = _market(marketSeed);
        (uint256 ghost0, uint256 ghost1,,,) = engine.states(marketId);
        uint256 available = zeroForOne ? ghost0 : ghost1;
        if (available == 0) return;

        uint256 maxAmount = _clamp(uint256(rawMaxAmount), 1, available);
        uint256 spotPrice = router.getSpotPrice(marketId);
        if (spotPrice == 0) return;

        uint256 fullPayment = zeroForOne ? (maxAmount * spotPrice) / PRICE_SCALE : (maxAmount * PRICE_SCALE) / spotPrice;
        if (fullPayment == 0) return;

        MockERC20 paymentToken = zeroForOne ? token1 : token0;
        _ensureFunded(paymentToken, SOLVER, fullPayment + 1e18);

        vm.prank(SOLVER);
        try engine.clearAuction(marketId, zeroForOne, maxAmount, 0) {} catch {}
    }

    function applyFairNetting(uint8 marketSeed, uint96 rawConsumed0) external {
        bytes32 marketId = _market(marketSeed);
        uint256 spotPrice = router.getSpotPrice(marketId);
        if (spotPrice == 0) return;

        vm.prank(address(router));
        (uint256 ghost0, uint256 ghost1) = engine.syncAndFetchGhost(marketId);
        if (ghost0 == 0 || ghost1 == 0) return;

        uint256 maxConsumed0ByGhost1 = (ghost1 * PRICE_SCALE) / spotPrice;
        if (maxConsumed0ByGhost1 == 0) return;

        uint256 maxConsumed0 = ghost0 < maxConsumed0ByGhost1 ? ghost0 : maxConsumed0ByGhost1;
        uint256 consumed0 = _clamp(uint256(rawConsumed0), 1, maxConsumed0);
        uint256 consumed1 = (consumed0 * spotPrice) / PRICE_SCALE;
        if (consumed1 == 0 || consumed1 > ghost1) return;

        vm.prank(address(router));
        try engine.applyNettingResult(marketId, consumed0, consumed1, spotPrice) {} catch {}
    }

    function takeGhost(uint8 actorSeed, uint8 marketSeed, bool zeroForOne, uint96 rawAmountIn) external {
        bytes32 marketId = _market(marketSeed);
        uint256 spotPrice = router.getSpotPrice(marketId);
        if (spotPrice == 0) return;

        uint256 amountIn = _clamp(uint256(rawAmountIn), 1, MAX_TAKER_INPUT);
        address actor = _actor(actorSeed);
        MockERC20 inputToken = zeroForOne ? token0 : token1;
        _ensureFunded(inputToken, actor, amountIn);

        // Mirror router behavior by pre-funding the vault with taker input.
        vm.prank(actor);
        inputToken.transfer(address(router), amountIn);

        vm.prank(address(router));
        try engine.takeGhost(marketId, zeroForOne, amountIn, spotPrice) returns (uint256, uint256) {} catch {}
    }

    function forceSettle(uint8 marketSeed, bool zeroForOne) external {
        bytes32 marketId = _market(marketSeed);
        (uint256 ghost0, uint256 ghost1,,,) = engine.states(marketId);
        uint256 ghostAmount = zeroForOne ? ghost0 : ghost1;
        if (ghostAmount == 0) return;

        (uint256 sellRateCurrent,) = engine.streamPools(marketId, zeroForOne);
        if (sellRateCurrent == 0) return;

        // Mock settle path returns configured amountOut but does not transfer tokens,
        // so pre-fund router with the expected buy token to keep accounting realistic.
        MockERC20 buyToken = zeroForOne ? token1 : token0;
        buyToken.mint(address(router), ghostAmount);
        router.setSettleOutOverride(marketId, ghostAmount);

        vm.prank(address(router));
        try engine.forceSettle(marketId, zeroForOne) {} catch {}
    }

    // ─────────────────────────────────────────────────────────────────────────────
    // Invariant helpers
    // ─────────────────────────────────────────────────────────────────────────────

    function trackedOrderCount() external view returns (uint256) {
        return trackedOrders.length;
    }

    function computeTokenLiabilities() external view returns (uint256 token0Liability, uint256 token1Liability) {
        // Ghost balances remain liabilities until consumed and distributed.
        (uint256 ghost0A, uint256 ghost1A,,,) = engine.states(marketA);
        (uint256 ghost0B, uint256 ghost1B,,,) = engine.states(marketB);
        token0Liability = ghost0A + ghost0B;
        token1Liability = ghost1A + ghost1B;

        for (uint256 i = 0; i < trackedOrders.length; ++i) {
            OrderRef memory ref = trackedOrders[i];
            if (!_orderExists(ref.marketId, ref.orderId)) continue;

            (uint256 buyTokensOwed, uint256 sellTokensRefund) = engine.getCancelOrderState(ref.marketId, ref.orderId);
            (,,,,, bool zeroForOne) = engine.streamOrders(ref.marketId, ref.orderId);

            if (zeroForOne) {
                token0Liability += sellTokensRefund;
                token1Liability += buyTokensOwed;
            } else {
                token1Liability += sellTokensRefund;
                token0Liability += buyTokensOwed;
            }
        }
    }

    function assertOrderMetadataConsistency() external view {
        for (uint256 i = 0; i < trackedOrders.length; ++i) {
            OrderRef memory ref = trackedOrders[i];
            if (!_orderExists(ref.marketId, ref.orderId)) continue;

            (address owner, uint256 sellRate,, uint256 startEpoch, uint256 expiration,) =
                engine.streamOrders(ref.marketId, ref.orderId);

            require(sellRate > 0, "active order must have sell rate");
            require(owner == ref.owner, "tracked owner mismatch");
            require(startEpoch % INTERVAL == 0, "start not interval aligned");
            require(expiration % INTERVAL == 0, "expiration not interval aligned");
            require(expiration > startEpoch, "bad order temporal bounds");
        }

        _assertMarketEpochInterval(marketA);
        _assertMarketEpochInterval(marketB);
    }

    // ─────────────────────────────────────────────────────────────────────────────
    // Internal helpers
    // ─────────────────────────────────────────────────────────────────────────────

    function _assertMarketEpochInterval(bytes32 marketId) internal view {
        (,,, uint256 lastClearTime, uint256 epochInterval) = engine.states(marketId);
        if (lastClearTime == 0) return;
        require(epochInterval == INTERVAL, "market epoch interval drift");
    }

    function _orderExists(bytes32 marketId, bytes32 orderId) internal view returns (bool) {
        (, uint256 sellRate,,,,) = engine.streamOrders(marketId, orderId);
        return sellRate > 0;
    }

    function _actor(uint256 actorSeed) internal pure returns (address) {
        uint256 slot = actorSeed % 3;
        if (slot == 0) return ALICE;
        if (slot == 1) return BOB;
        return CAROL;
    }

    function _market(uint256 marketSeed) internal view returns (bytes32) {
        return marketSeed % 2 == 0 ? marketA : marketB;
    }

    function _ensureFunded(MockERC20 token, address account, uint256 requiredBalance) internal {
        uint256 bal = token.balanceOf(account);
        if (bal < requiredBalance) {
            token.mint(account, requiredBalance - bal + 1e18);
        }
    }

    function _clamp(uint256 value, uint256 minValue, uint256 maxValue) internal pure returns (uint256) {
        if (value < minValue) return minValue;
        if (value > maxValue) return maxValue;
        return value;
    }

    function _activeOrder(uint256 seed) internal view returns (bool found, OrderRef memory ref) {
        uint256 len = trackedOrders.length;
        if (len == 0) return (false, ref);

        uint256 start = seed % len;
        for (uint256 i = 0; i < len; ++i) {
            uint256 idx = (start + i) % len;
            OrderRef memory candidate = trackedOrders[idx];
            if (_orderExists(candidate.marketId, candidate.orderId)) {
                return (true, candidate);
            }
        }
        return (false, ref);
    }
}
