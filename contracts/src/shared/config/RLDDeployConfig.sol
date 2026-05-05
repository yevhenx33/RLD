// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/**
 * @title RLDDeployConfig
 * @author RLD Protocol
 * @notice Single source of truth for all mainnet addresses and protocol parameters
 *         used across deployment scripts, unit tests, and integration tests.
 *
 * @dev USAGE
 * ─────────────────────────────────────────────────────────────────────────────
 *
 *   import {RLDDeployConfig as C} from "../src/shared/config/RLDDeployConfig.sol";
 *
 *   // Now use constants directly:
 *   IPoolManager pm = IPoolManager(C.POOL_MANAGER);
 *   address collateral = C.AUSDC;
 *
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * @dev GUIDELINES FOR UPDATING VALUES
 *
 *   1. All addresses must be checksummed (mixed-case EIP-55 format).
 *   2. Only add addresses that are *permanent* mainnet deployments.
 *      Contract addresses deployed by OUR scripts (RLDCore, Factory, etc.)
 *      are NOT stored here — they change per deployment.
 *   3. Protocol config values (FUNDING_PERIOD, TWAMM_EXPIRATION_INTERVAL)
 *      represent the *default production values*. Scripts may override them
 *      for testing, but the defaults here define what ships to mainnet.
 *   4. After changing any value, run the full test suite:
 *        forge test --match-path test/unit/factory/RLDMarketFactory.t.sol -vvv
 *
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * @dev EXAMPLE: Adding a new market (e.g., aDAI)
 *
 *   // Add to the Aave V3 section:
 *   address internal constant ADAI = 0x018008bfb33d285247A21d44E50697654f754e63;
 *   address internal constant DAI  = 0x6B175474E89094C44Da98b954EedeAC495271d0F;
 *
 *   // The deploy script can then create a second market:
 *   factory.createMarket(DeployParams({
 *       underlyingPool: C.AAVE_POOL,
 *       underlyingToken: C.DAI,
 *       collateralToken: C.ADAI,
 *       ...
 *   }));
 */
library RLDDeployConfig {
    /* ======================================================================
       UNISWAP V4 — Immutable singleton infrastructure
    ====================================================================== */

    /// @notice Uniswap V4 PoolManager (singleton, manages all V4 pools)
    address internal constant POOL_MANAGER =
        0x000000000004444c5dc75cB358380D2e3dE08A90;

    /// @notice Uniswap V4 PositionManager (manages LP NFTs)
    address internal constant POSITION_MANAGER =
        0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;

    /// @notice Uniswap Permit2 (gasless approvals)
    address internal constant PERMIT2 =
        0x000000000022D473030F116dDEE9F6B43aC78BA3;

    /// @notice Deterministic CREATE2 deployer (used for hook salt mining)
    address internal constant CREATE2_DEPLOYER =
        0x4e59b44847b379578588920cA78FbF26c0B4956C;

    /* ======================================================================
       AAVE V3 — Lending pool and token addresses
    ====================================================================== */

    /// @notice Aave V3 Pool (the lending pool contract)
    address internal constant AAVE_POOL =
        0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    /// @notice Aave V3 aUSDC (yield-bearing USDC receipt token)
    address internal constant AUSDC =
        0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;

    /// @notice USDC stablecoin
    address internal constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;

    /* ======================================================================
       PROTOCOL CONFIGURATION — Default production values
    ====================================================================== */

    /// @notice Default funding period for continuous rate accrual
    /// @dev Controls how fast the normalization factor moves. 30 days is the
    ///      production default. A shorter period amplifies funding payments.
    uint32 internal constant FUNDING_PERIOD = 30 days;

    /// @notice TWAMM order expiration granularity
    /// @dev Orders must expire at multiples of this interval (1 hour).
    ///      Smaller = more frequent execution but higher gas costs.
    uint256 internal constant TWAMM_EXPIRATION_INTERVAL = 3600;

    /* ======================================================================
       RISK PARAMETERS — Default aUSDC market configuration
    ====================================================================== */

    /// @notice Minimum collateralization ratio to open or maintain a new position
    /// @dev 120% means $1.20 of collateral per $1.00 of debt.
    ///      Must be strictly > 100% (1e18). Higher = safer but less capital-efficient.
    uint64 internal constant MIN_COL_RATIO = 1.2e18;

    /// @notice Maintenance margin — below this, a position becomes liquidatable
    /// @dev 109% means liquidation triggers when collateral drops below $1.09 per $1 debt.
    ///      Must be ≤ MIN_COL_RATIO and > 100% (1e18).
    uint64 internal constant MAINTENANCE_MARGIN = 1.09e18;

    /// @notice Maximum portion of debt liquidatable in a single transaction
    /// @dev 50% prevents complete position wipeout, giving users time to respond.
    ///      Must be in range (0, 1e18].
    uint64 internal constant LIQUIDATION_CLOSE_FACTOR = 0.5e18;

    /// @notice Default minimum liquidation amount ($100 in 6-decimal USDC)
    /// @dev Can be raised per-market by curator, but never below $100.
    uint128 internal constant MIN_LIQUIDATION = 100e6;

    /* ======================================================================
       LIQUIDATION — Dutch Auction parameters (packed into bytes32)
    ====================================================================== */

    /// @notice Base liquidation discount in basis points
    /// @dev 100 bps = 1% minimum bonus for liquidators at healthy positions
    uint256 internal constant LIQ_BASE_DISCOUNT_BPS = 100;

    /// @notice Maximum liquidation discount in basis points
    /// @dev 500 bps = 5% cap prevents excessive MEV extraction
    uint256 internal constant LIQ_MAX_DISCOUNT_BPS = 500;

    /// @notice Liquidation slope (scaled by 100, so 200 = 2.0x)
    /// @dev Controls how quickly the discount ramps as health deteriorates.
    ///      Bonus = BaseDiscount + Slope × (1 - HealthScore)
    uint256 internal constant LIQ_SLOPE = 200;

    /// @notice Pre-packed liquidation params for the DutchLiquidationModule
    /// @dev Layout: [0..15] baseDiscount | [16..31] maxDiscount | [32..47] slope
    bytes32 internal constant LIQUIDATION_PARAMS =
        bytes32(
            uint256(LIQ_BASE_DISCOUNT_BPS) |
                (uint256(LIQ_MAX_DISCOUNT_BPS) << 16) |
                (uint256(LIQ_SLOPE) << 32)
        );

    /* ======================================================================
       UNISWAP V4 POOL — Default pool configuration for RLD markets
    ====================================================================== */

    /// @notice Pool swap fee in hundredths of a bip (3000 = 0.30%)
    /// @dev Standard fee tier for medium-volatility pairs.
    uint24 internal constant POOL_FEE = 3000;

    /// @notice Tick spacing for the V4 pool
    /// @dev 60 is standard for 0.30% fee pools.
    ///      Smaller = more granular LP positions but higher gas.
    int24 internal constant TICK_SPACING = 60;

    /// @notice TWAP observation window for the V4 oracle
    /// @dev Demo Reth deployments use a 60-second TWAP so the oracle can be
    ///      primed by waiting one minute before user setup.
    uint32 internal constant ORACLE_PERIOD = 60;

    /* ======================================================================
       MARKET METADATA — Default aUSDC market naming
    ====================================================================== */

    /// @notice Default position token name
    string internal constant POSITION_TOKEN_NAME = "Wrapped RLP: aUSDC";

    /// @notice Default position token symbol
    string internal constant POSITION_TOKEN_SYMBOL = "wRLPaUSDC";

    /* ======================================================================
       TWAMM HOOK FLAGS — Permission bits for V4 hook address mining
    ====================================================================== */

    /// @notice Combined hook permission flags for salt mining
    /// @dev BEFORE_INITIALIZE | BEFORE_ADD_LIQUIDITY | BEFORE_REMOVE_LIQUIDITY |
    ///      BEFORE_SWAP | AFTER_SWAP
    uint160 internal constant TWAMM_HOOK_FLAGS =
        uint160(
            (1 << 13) | // BEFORE_INITIALIZE_FLAG
                (1 << 11) | // BEFORE_ADD_LIQUIDITY_FLAG
                (1 << 9) | // BEFORE_REMOVE_LIQUIDITY_FLAG
                (1 << 7) | // BEFORE_SWAP_FLAG
                (1 << 4) | // AFTER_SWAP_FLAG
                (1 << 2) // BEFORE_SWAP_RETURNS_DELTA_FLAG
        );
}
