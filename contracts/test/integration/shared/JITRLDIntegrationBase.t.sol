// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";

// RLD Core
import {RLDMarketFactory} from "../../../src/rld/core/RLDMarketFactory.sol";
import {RLDCore} from "../../../src/rld/core/RLDCore.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";

// Tokens and Brokers
import {PositionToken} from "../../../src/rld/tokens/PositionToken.sol";
import {PrimeBroker} from "../../../src/rld/broker/PrimeBroker.sol";
import {BrokerRouter} from "../../../src/periphery/BrokerRouter.sol";

// Modules (all real, matching DeployRLDProtocol.s.sol)
import {
    DutchLiquidationModule
} from "../../../src/rld/modules/liquidation/DutchLiquidationModule.sol";
import {
    StandardFundingModel
} from "../../../src/rld/modules/funding/StandardFundingModel.sol";
import {
    UniswapV4SingletonOracle
} from "../../../src/rld/modules/oracles/UniswapV4SingletonOracle.sol";
import {
    UniswapV4BrokerModule
} from "../../../src/rld/modules/broker/UniswapV4BrokerModule.sol";
import {
    JTMBrokerModule
} from "../../../src/rld/modules/broker/JTMBrokerModule.sol";
import {
    RLDAaveOracle
} from "../../../src/rld/modules/oracles/RLDAaveOracle.sol";
import {IRLDOracle} from "../../../src/shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../../src/shared/interfaces/ISpotOracle.sol";

// Uniswap V4
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {FullMath} from "v4-core/src/libraries/FullMath.sol";
import {JTM} from "../../../src/twamm/JTM.sol";
import {HookMiner} from "v4-periphery/src/utils/HookMiner.sol";

// V4 Periphery (PositionManager)
import {PositionManager} from "v4-periphery/src/PositionManager.sol";
import {
    IPositionDescriptor
} from "v4-periphery/src/interfaces/IPositionDescriptor.sol";
import {IWETH9} from "v4-periphery/src/interfaces/external/IWETH9.sol";
import {
    IAllowanceTransfer
} from "permit2/src/interfaces/IAllowanceTransfer.sol";

// Permit2 deployment helper — ships with v4-periphery test utilities.
// Uses vm.etch of precompiled bytecode so we don't need to compile Permit2
// with via-ir. Identical approach to PosmTestSetup.sol in v4-periphery tests.
import {DeployPermit2} from "permit2/test/utils/DeployPermit2.sol";

// Test ERC20
import {MockERC20} from "solmate/src/test/utils/mocks/MockERC20.sol";

/* ============================================================ */
/*              MINIMAL PRODUCTION HELPER CONTRACTS             */
/*    Mirror the contracts defined inline in DeployRLDProtocol  */
/* ============================================================ */

/// @dev Required by RLDMarketFactory but irrelevant for integration tests.
///      Same as the one in DeployRLDProtocol.s.sol.
contract MinimalMetadataRenderer {
    function tokenURI(uint256) external pure returns (string memory) {
        return "";
    }
}

/// @dev Configurable oracle used in place of the live RLDAaveOracle.
///      Tests don't fork Aave, so we need a price knob. This contract
///      satisfies both IRLDOracle (index) and ISpotOracle (mark) interfaces,
///      making it usable for both rateOracle and spotOracle slots.
///
///      IMPORTANT: This is the ONLY mock remaining. Every other contract
///                 is the identical production implementation.
contract ConfigurableOracle is IRLDOracle, ISpotOracle {
    uint256 public indexPrice = 5e18; // default: 5% Aave rate  (K=100, rate=5%)
    uint256 public spotPrice = 5e18; // default: same as index

    /// @notice Call this from tests to simulate a specific Aave borrow rate.
    ///         Formula matching RLDAaveOracle: P = (borrowRateRAY × 100) / 1e9
    function setIndexPrice(uint256 _price) external {
        indexPrice = _price;
    }

    function setSpotPrice(uint256 _price) external {
        spotPrice = _price;
    }

    function getIndexPrice(address, address) external view returns (uint256) {
        return indexPrice;
    }

    function getMarkPrice(address, address) external view returns (uint256) {
        return spotPrice;
    }

    function getSpotPrice(address, address) external view returns (uint256) {
        return spotPrice;
    }
}

/// @dev PositionToken implementation clone used by RLDMarketFactory.
///      Matches the approach in DeployRLDProtocol.s.sol which also just needs
///      a non-zero, syntactically valid token address.
contract PositionTokenImpl is PositionToken {
    constructor() PositionToken("Implementation", "IMPL", 18, address(1)) {}
}

/* ============================================================ */
/*                 CANONICAL PERMIT2 ADDRESS                    */
/* ============================================================ */

/// @dev Permit2 canonical deterministic address (same on all chains/testnets).
address constant PERMIT2_ADDRESS = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

/* ============================================================ */
/*                      BASE SETUP CONTRACT                     */
/* ============================================================ */

/**
 * @title JITRLDIntegrationBase
 * @notice Shared setUp for all RLD integration tests.
 *
 * Deployment order mirrors `DeployRLDProtocol.s.sol` exactly:
 *
 *  Phase 0 – Infra: Permit2 (vm.etch), PoolManager, PositionManager
 *  Phase 1 – Modules: DutchLiquidation, StandardFunding, V4Oracle, V4BrokerModule, JTMBrokerModule
 *  Phase 2 – JTM hook (HookMiner + CREATE2)
 *  Phase 3 – V4 pool initialization (PT 18-dec / CT 6-dec)
 *  Phase 4 – Templates: PositionToken impl, PrimeBroker impl
 *  Phase 5 – BrokerRouter
 *  Phase 6 – RLDMarketFactory + RLDCore
 *  Phase 7 – First RLD market
 *
 * The only non-production contracts are:
 *   - MockERC20 for PT and CT (no real tokens on a dev network)
 *   - ConfigurableOracle for rate/spot prices (no Aave mainnet fork)
 *
 * Override `_tweakSetup()` for subclass customisation without reimplementing setUp.
 */
abstract contract JITRLDIntegrationBase is Test, DeployPermit2 {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;

    // ----------------------------------------------------------------
    //  Infra
    // ----------------------------------------------------------------
    IPoolManager public poolManager;
    PositionManager public positionManager;

    // ----------------------------------------------------------------
    //  Singleton Modules  (mirrors Phase 1 of DeployRLDProtocol.s.sol)
    // ----------------------------------------------------------------
    DutchLiquidationModule public liqModule;
    StandardFundingModel public fundingModel;
    UniswapV4SingletonOracle public v4Oracle; // mark oracle (pool price)
    UniswapV4BrokerModule public v4BrokerModule;
    JTMBrokerModule public twammBrokerModule;
    ConfigurableOracle public testOracle; // rate + spot oracle (configurable)
    MinimalMetadataRenderer public metadataRenderer;

    // ----------------------------------------------------------------
    //  RLD Core  (mirrors Phase 3–6 of DeployRLDProtocol.s.sol)
    // ----------------------------------------------------------------
    RLDMarketFactory public rldFactory;
    RLDCore public core;
    BrokerRouter public brokerRouter;

    // ----------------------------------------------------------------
    //  JTM Hook  (mirrors Phase 2 of DeployRLDProtocol.s.sol)
    // ----------------------------------------------------------------
    JTM public twammHook;
    PoolKey public twammPoolKey;

    // ----------------------------------------------------------------
    //  Tokens  (mock ERC20s — factory forces PT.decimals == CT.decimals)
    // ----------------------------------------------------------------
    MockERC20 public pt; // Position Token  (e.g. wRLP)    — 6 dec
    MockERC20 public ct; // Collateral Token (e.g. waUSDC) — 6 dec

    // ----------------------------------------------------------------
    //  RLD Market
    // ----------------------------------------------------------------
    MarketId public marketId;
    address public wrlpToken;

    // ----------------------------------------------------------------
    //  Constants  (match RLDDeployConfig constants where applicable)
    // ----------------------------------------------------------------

    /// @dev sqrtPriceX96 = √1 × 2^96  (1:1 price ratio, raw units)
    uint160 constant SQRT_PRICE_1_1 = 79228162514264337593543950336;

    /// @dev JTM order expiration interval (1 hour)
    uint256 constant JTM_EXPIRATION_INTERVAL = 3600;

    /// @dev Tick spacing for the JTM pool (matches production market configs)
    int24 constant TICK_SPACING = 60;

    /// @dev Fee tier: 0.3% (matches production)
    uint24 constant FEE = 3000;

    /// @dev Funding period: 30 days (matches RLDDeployConfig.FUNDING_PERIOD)
    uint32 constant FUNDING_PERIOD = 30 days;

    /// @dev Aave oracle K scalar (from RLDAaveOracle.K_SCALAR)
    uint256 constant AAVE_K_SCALAR = 100;

    // ----------------------------------------------------------------
    //  setUp — mirrors DeployRLDProtocol.s.sol deployment phases
    // ----------------------------------------------------------------
    function setUp() public virtual {
        // ─────────────────────────────────────────────────────────────
        // Phase 0: Infrastructure
        // ─────────────────────────────────────────────────────────────

        // Permit2 — etched at its canonical deterministic address.
        // Identical technique to PosmTestSetup.sol in v4-periphery.
        _deployPermit2();

        // Real Uniswap V4 PoolManager
        poolManager = new PoolManager(address(this));

        // Real Uniswap V4 PositionManager (POSM)
        // - tokenDescriptor: address(0) → no on-chain SVG (same as test configs)
        // - weth9: address(0) → no native ETH wrapping needed in tests
        positionManager = new PositionManager(
            poolManager,
            IAllowanceTransfer(PERMIT2_ADDRESS),
            300_000, // unsubscribeGasLimit
            IPositionDescriptor(address(0)), // no descriptor
            IWETH9(address(0)) // no WETH9
        );

        // Test tokens — both 6-dec (factory clones PT with CT's decimals)
        pt = new MockERC20("Position Token", "PT", 6); // e.g. wRLP
        ct = new MockERC20("Collateral Token", "CT", 6); // e.g. waUSDC
        pt.mint(address(this), 1_000_000_000e6);
        ct.mint(address(this), 1_000_000_000e6);

        // ─────────────────────────────────────────────────────────────
        // Phase 1: Singleton Modules
        // Exact order from DeployRLDProtocol.s.sol Phase 0 + Phase 1
        // ─────────────────────────────────────────────────────────────

        metadataRenderer = new MinimalMetadataRenderer();
        v4BrokerModule = new UniswapV4BrokerModule();
        twammBrokerModule = new JTMBrokerModule();

        liqModule = new DutchLiquidationModule();
        fundingModel = new StandardFundingModel();
        v4Oracle = new UniswapV4SingletonOracle(); // mark oracle
        testOracle = new ConfigurableOracle(); // rate + spot (configurable)

        // ─────────────────────────────────────────────────────────────
        // Phase 2: JTM Hook — HookMiner + CREATE2
        // Mirrors DeployRLDProtocol.s.sol exactly.
        // Flags: beforeInitialize | beforeAddLiquidity | beforeRemoveLiquidity
        //        | beforeSwap | afterSwap | beforeSwapReturnDelta
        // ─────────────────────────────────────────────────────────────
        {
            uint160 flags = uint160(
                Hooks.BEFORE_INITIALIZE_FLAG |
                    Hooks.BEFORE_ADD_LIQUIDITY_FLAG |
                    Hooks.BEFORE_REMOVE_LIQUIDITY_FLAG |
                    Hooks.BEFORE_SWAP_FLAG |
                    Hooks.AFTER_SWAP_FLAG |
                    Hooks.BEFORE_SWAP_RETURNS_DELTA_FLAG
            );
            bytes memory creationCode = type(JTM).creationCode;
            bytes memory constructorArgs = abi.encode(
                poolManager,
                JTM_EXPIRATION_INTERVAL,
                address(this), // initialOwner (test contract)
                address(0) // rldCore — wired later via twammHook.setRldCore()
            );
            (address hookAddress, bytes32 salt) = HookMiner.find(
                address(this),
                flags,
                creationCode,
                constructorArgs
            );
            twammHook = new JTM{salt: salt}(
                poolManager,
                JTM_EXPIRATION_INTERVAL,
                address(this),
                address(0)
            );
            require(
                address(twammHook) == hookAddress,
                "JITRLDIntegrationBase: JTM address mismatch"
            );
        }

        // ─────────────────────────────────────────────────────────────
        // Phase 3: V4 Pool Initialization
        // Sort tokens (V4: currency0 < currency1 by address)
        // ─────────────────────────────────────────────────────────────
        {
            (Currency c0, Currency c1) = _sortCurrencies(
                Currency.wrap(address(pt)),
                Currency.wrap(address(ct))
            );
            twammPoolKey = PoolKey({
                currency0: c0,
                currency1: c1,
                fee: FEE,
                tickSpacing: TICK_SPACING,
                hooks: IHooks(address(twammHook))
            });
            poolManager.initialize(twammPoolKey, _initialSqrtPrice());
        }

        // ─────────────────────────────────────────────────────────────
        // Phase 4: Implementation Templates
        // Mirrors DeployRLDProtocol.s.sol Phase 2
        // PrimeBroker gets the real valuation modules + real POSM
        // ─────────────────────────────────────────────────────────────
        PositionTokenImpl posImpl = new PositionTokenImpl();
        PrimeBroker brokerImpl = new PrimeBroker(
            address(v4BrokerModule), // _v4Module   (real UniswapV4BrokerModule)
            address(twammBrokerModule), // _twammModule (real JTMBrokerModule)
            address(positionManager) // _posm        (real V4 PositionManager)
        );

        // ─────────────────────────────────────────────────────────────
        // Phase 5: BrokerRouter
        // Mirrors DeployRLDProtocol.s.sol Phase 2.5
        // ─────────────────────────────────────────────────────────────
        brokerRouter = new BrokerRouter(address(poolManager), PERMIT2_ADDRESS);

        // ─────────────────────────────────────────────────────────────
        // Phase 6: Factory + Core
        // Mirrors DeployRLDProtocol.s.sol Phase 3 + 4
        // ─────────────────────────────────────────────────────────────
        rldFactory = new RLDMarketFactory(
            address(poolManager),
            address(posImpl),
            address(brokerImpl),
            address(v4Oracle), // mark oracle (V4SingletonOracle)
            address(fundingModel), // real StandardFundingModel
            address(twammHook),
            address(metadataRenderer), // real MinimalMetadataRenderer
            FUNDING_PERIOD,
            address(brokerRouter) // real BrokerRouter
        );

        core = new RLDCore(
            address(rldFactory),
            address(poolManager),
            address(0)
        );
        rldFactory.initializeCore(address(core));

        // F-04: Transfer oracle ownership to factory (so factory can registerPool)
        v4Oracle.transferOwnership(address(rldFactory));

        // Wire JTM hook to core (matches production DeployRLDProtocol.s.sol Phase 5)
        twammHook.setRldCore(address(core));

        // ─────────────────────────────────────────────────────────────
        // Phase 6b: Price Bounds — set once by deployer (owner)
        // price 0.0001 → sqrtPriceX96 = sqrt(0.0001) × 2^96
        // price 100    → sqrtPriceX96 = sqrt(100)    × 2^96
        // ─────────────────────────────────────────────────────────────
        twammHook.setPriceBounds(
            twammPoolKey,
            uint160(792281625142643392428113920), // sqrtPriceX96(0.0001)
            uint160(792281625142643375935439503360) // sqrtPriceX96(100)
        );

        // ─────────────────────────────────────────────────────────────
        // Phase 7: First RLD Market
        // Wire factory as authorized caller for setPriceBounds
        // ─────────────────────────────────────────────────────────────
        twammHook.setAuthorizedFactory(address(rldFactory));
        (marketId, ) = rldFactory.createMarket(_defaultMarketParams());
        IRLDCore.MarketAddresses memory ma = core.getMarketAddresses(marketId);
        wrlpToken = ma.positionToken;

        // FIN-01: Factory defaults debtCap=0. Set unlimited cap for tests.
        _setUnlimitedDebtCap();

        // ─────────────────────────────────────────────────────────────
        // Phase 8: Seed TWAP oracle with a second observation
        // The oracle needs cardinality >= 2 for TWAP (avoids OracleNotReady).
        // grow(10) in _beforeInitialize sets cardinalityNext=10; we need
        // one more write at a different timestamp to bump cardinality to 10.
        // ─────────────────────────────────────────────────────────────
        // Warp to the next interval boundary so lastUpdateTimestamp stays clean
        uint256 currentInterval = (block.timestamp / JTM_EXPIRATION_INTERVAL) *
            JTM_EXPIRATION_INTERVAL;
        vm.warp(currentInterval + JTM_EXPIRATION_INTERVAL);
        twammHook.executeJTMOrders(twammPoolKey);

        // Optional subclass customisation hook
        _tweakSetup();
    }

    /// @dev Override in derived tests to adjust state after base setUp completes.
    ///      E.g., set oracle prices, seed LP positions, open broker accounts.
    function _tweakSetup() internal virtual {}

    /// @dev FIN-01: Factory defaults debtCap=0. This helper proposes a risk update
    ///      with unlimited debt cap (type(uint128).max), then warps past the 7-day
    ///      timelock so the config becomes effective for testing.
    function _setUnlimitedDebtCap() internal {
        IRLDCore.MarketConfig memory cfg = core.getMarketConfig(marketId);
        core.proposeRiskUpdate(
            marketId,
            cfg.minColRatio,
            cfg.maintenanceMargin,
            cfg.liquidationCloseFactor,
            cfg.fundingPeriod,
            cfg.badDebtPeriod,
            type(uint128).max, // unlimited debt cap
            cfg.minLiquidation,
            cfg.liquidationParams
        );
        vm.warp(block.timestamp + 7 days + 1);
    }

    /// @dev Override in derived tests to change pool initialization price.
    ///      Default = 1:1 (SQRT_PRICE_1_1). E2E tests should override to
    ///      match the oracle index price.
    function _initialSqrtPrice() internal virtual returns (uint160) {
        return SQRT_PRICE_1_1;
    }

    // ----------------------------------------------------------------
    //  Permit2 bootstrap
    // ----------------------------------------------------------------

    /// @dev Deploy Permit2 at its canonical deterministic address.
    ///      Delegates to DeployPermit2.deployPermit2() from the v4-periphery library
    ///      which uses vm.etch with the full pre-compiled bytecode.
    ///      Identical approach to PosmTestSetup.sol in v4-periphery tests.
    function _deployPermit2() internal {
        deployPermit2(); // from DeployPermit2 base contract
    }

    // ----------------------------------------------------------------
    //  Price math helpers (mirrors CalculateSwapAmount.s.sol exactly)
    // ----------------------------------------------------------------

    /**
     * @dev Compute V4 sqrtPriceX96 from a WAD-denominated, decimal-adjusted price.
     *      Formula: sqrtPriceX96 = sqrt(priceWAD × 2^192 / 1e18)
     *
     *      To derive from an oracle index price:
     *        1. rawPrice_WAD = _decimalAdjustPrice(indexPrice_WAD, dec0, dec1)
     *        2. sqrtPriceX96 = _computeSqrtPriceX96(rawPrice_WAD)
     *
     *      dec0 = decimals of currency0 in the pool (PT=18 or CT=6)
     *      dec1 = decimals of currency1 in the pool (PT=18 or CT=6)
     */
    function _computeSqrtPriceX96(
        uint256 priceWAD
    ) internal pure returns (uint160) {
        uint256 priceQ192 = FullMath.mulDiv(priceWAD, 1 << 192, 1e18);
        return uint160(_sqrt(priceQ192));
    }

    /**
     * @dev Apply decimal adjustment: oracle WAD price → pool raw price (token1/token0 units).
     *      For PT(18-dec) as token0, CT(6-dec) as token1:
     *        rawPrice = indexPrice × 10^(18−6) = indexPrice × 1e12
     *      For CT(6-dec) as token0, PT(18-dec) as token1:
     *        rawPrice = indexPrice / 10^(18−6) = indexPrice / 1e12
     */
    function _decimalAdjustPrice(
        uint256 priceWAD,
        uint256 dec0,
        uint256 dec1
    ) internal pure returns (uint256 rawPriceWAD) {
        rawPriceWAD = dec0 >= dec1
            ? FullMath.mulDiv(priceWAD, 10 ** (dec0 - dec1), 1)
            : FullMath.mulDiv(priceWAD, 1, 10 ** (dec1 - dec0));
    }

    /// @dev Babylonian integer square root — identical to CalculateSwapAmount.s.sol.
    function _sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }

    function _sortCurrencies(
        Currency a,
        Currency b
    ) internal pure returns (Currency, Currency) {
        return Currency.unwrap(a) < Currency.unwrap(b) ? (a, b) : (b, a);
    }

    // ----------------------------------------------------------------
    //  Default market params
    // ----------------------------------------------------------------

    function _defaultMarketParams()
        internal
        view
        returns (RLDMarketFactory.DeployParams memory)
    {
        // In the sorted pool: currency1 = collateral, currency0 = base (wRLP-denominated)
        address col = Currency.unwrap(twammPoolKey.currency1);
        address base = Currency.unwrap(twammPoolKey.currency0);

        // underlyingPool is derived from the PoolId (same as production scripts)
        bytes32 poolId_ = PoolId.unwrap(twammPoolKey.toId());
        address underlyingPoolAddr = address(uint160(uint256(poolId_)));

        return
            RLDMarketFactory.DeployParams({
                underlyingPool: underlyingPoolAddr,
                underlyingToken: base,
                collateralToken: col,
                curator: address(this),
                positionTokenName: "Integration wRLP",
                positionTokenSymbol: "iwRLP",
                minColRatio: 1.2e18,
                maintenanceMargin: 1.1e18,
                liquidationCloseFactor: 0.5e18,
                liquidationModule: address(liqModule),
                // Packed: [slope=100 << 32 | maxDiscount=1000 << 16 | baseDiscount=0]
                // base=0%, max=10% (1000 bps), slope=1.0x (scaled 100)
                liquidationParams: bytes32(
                    (uint256(100) << 32) | (uint256(1000) << 16) | uint256(0)
                ),
                spotOracle: address(testOracle), // configurable spot price
                rateOracle: address(testOracle), // configurable index price
                oraclePeriod: 3600,
                poolFee: FEE,
                tickSpacing: TICK_SPACING
            });
    }
}
