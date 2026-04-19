// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";

import {GhostRouter} from "../../src/dex/GhostRouter.sol";
import {TwapEngine} from "../../src/dex/TwapEngine.sol";
import {LimitEngine} from "../../src/dex/LimitEngine.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockGhostOracle} from "./mocks/MockGhostOracle.sol";
import {MockPoolManager} from "./mocks/MockPoolManager.sol";

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

contract TwapLimitGasBenchTest is Test {
    struct GasBenchResult {
        uint256 twapSubmitTotalGas;
        uint256 limitSubmitTotalGas;
        uint256 swapGas;
        uint256 swapOut;
    }

    uint256 internal constant INTERVAL = 60;
    uint256 internal constant TWAP_DURATION = 600;
    uint256 internal constant ORDER_AMOUNT = 1e18;
    uint256 internal constant LIMIT_TRIGGER = 2e18;
    uint256 internal constant SPOT_PRICE = 1e18;
    uint256 internal constant SWAP_IN = 8e18;

    address internal maker = address(0xA11CE);
    address internal taker = address(0xCAFE);

    MockERC20 internal tokenA;
    MockERC20 internal tokenB;
    MockPoolManager internal poolManager;
    MockGhostOracle internal oracle;
    GhostRouter internal router;
    TwapEngine internal twapEngine;
    LimitEngine internal limitEngine;

    address internal token0;
    address internal token1;
    PoolKey internal key;
    bytes32 internal marketId;

    function setUp() public {
        tokenA = new MockERC20("TokenA", "TKA", 18);
        tokenB = new MockERC20("TokenB", "TKB", 18);
        poolManager = new MockPoolManager();
        oracle = new MockGhostOracle();

        (token0, token1) =
            address(tokenA) < address(tokenB) ? (address(tokenA), address(tokenB)) : (address(tokenB), address(tokenA));

        key = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 3000,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });

        router = new GhostRouter(address(poolManager), address(this));
        marketId = router.initializeMarket(key, address(oracle));
        oracle.setPrice(marketId, SPOT_PRICE);

        twapEngine = new TwapEngine(address(router), INTERVAL, 500, 0);
        limitEngine = new LimitEngine(address(router));
        router.registerEngine(address(twapEngine));
        router.registerEngine(address(limitEngine));

        MockERC20 t0 = token0 == address(tokenA) ? tokenA : tokenB;
        MockERC20 t1 = token0 == address(tokenA) ? tokenB : tokenA;

        // Need enough inventory for 1000 TWAP + 1000 limit orders.
        uint256 makerInventory = 3_000e18;
        t0.mint(maker, makerInventory);
        t1.mint(maker, makerInventory);
        t0.mint(taker, 1_000e18);
        t1.mint(taker, 1_000e18);

        vm.startPrank(maker);
        t0.approve(address(router), type(uint256).max);
        t1.approve(address(router), type(uint256).max);
        vm.stopPrank();

        vm.startPrank(taker);
        t0.approve(address(router), type(uint256).max);
        t1.approve(address(router), type(uint256).max);
        vm.stopPrank();
    }

    function test_gasBench_10OrdersPerEngine() external {
        GasBenchResult memory result = _runBench(10, false);
        _logResult("same_bucket", 10, result);
    }

    function test_gasBench_100OrdersPerEngine() external {
        GasBenchResult memory result = _runBench(100, false);
        _logResult("same_bucket", 100, result);
    }

    function test_gasBench_1000OrdersPerEngine() external {
        GasBenchResult memory result = _runBench(1000, false);
        _logResult("same_bucket", 1000, result);
    }

    function test_gasBenchFragmented_10OrdersPerEngine() external {
        GasBenchResult memory result = _runBench(10, true);
        _logResult("fragmented_bucket", 10, result);
    }

    function test_gasBenchFragmented_100OrdersPerEngine() external {
        GasBenchResult memory result = _runBench(100, true);
        _logResult("fragmented_bucket", 100, result);
    }

    function test_gasBenchFragmented_1000OrdersPerEngine() external {
        GasBenchResult memory result = _runBench(1000, true);
        _logResult("fragmented_bucket", 1000, result);
    }

    function _runBench(uint256 orderCount, bool fragmentedLimitTriggers)
        internal
        returns (GasBenchResult memory result)
    {
        for (uint256 i = 0; i < orderCount; ++i) {
            vm.prank(maker);
            uint256 startGas = gasleft();
            twapEngine.submitStream(marketId, false, TWAP_DURATION, ORDER_AMOUNT);
            result.twapSubmitTotalGas += (startGas - gasleft());
        }

        for (uint256 i = 0; i < orderCount; ++i) {
            uint256 triggerPrice = fragmentedLimitTriggers ? (LIMIT_TRIGGER + i) : LIMIT_TRIGGER;
            vm.prank(maker);
            uint256 startGas = gasleft();
            limitEngine.submitLimitOrder(marketId, false, triggerPrice, ORDER_AMOUNT);
            result.limitSubmitTotalGas += (startGas - gasleft());
        }

        uint256 nextEpoch = ((block.timestamp / INTERVAL) * INTERVAL) + INTERVAL;
        vm.warp(nextEpoch + 120);

        vm.prank(taker);
        uint256 swapStartGas = gasleft();
        result.swapOut = router.swap(marketId, true, SWAP_IN, 1);
        result.swapGas = swapStartGas - gasleft();

        assertGt(result.swapOut, 0, "benchmark swap must produce output");
    }

    function _logResult(string memory mode, uint256 orderCount, GasBenchResult memory result) internal {
        emit log("----- TWAP + Limit Simultaneous Benchmark -----");
        emit log_named_string("limit_trigger_mode", mode);
        emit log_named_uint("orders_per_engine", orderCount);
        emit log_named_uint("total_orders", orderCount * 2);
        emit log_named_uint("twap_submit_total_gas", result.twapSubmitTotalGas);
        emit log_named_uint("twap_submit_avg_gas", result.twapSubmitTotalGas / orderCount);
        emit log_named_uint("limit_submit_total_gas", result.limitSubmitTotalGas);
        emit log_named_uint("limit_submit_avg_gas", result.limitSubmitTotalGas / orderCount);
        emit log_named_uint("swap_gas_with_both_engines_active", result.swapGas);
        emit log_named_uint("swap_amount_out", result.swapOut);
    }
}
