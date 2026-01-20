// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {IERC20} from "../interfaces/IERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ILendingAdapter} from "../interfaces/ILendingAdapter.sol";

// Uniswap V4
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "@uniswap/v4-core/src/types/PoolId.sol";
import {Currency, CurrencyLibrary} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {SafeCast} from "@uniswap/v4-core/src/libraries/SafeCast.sol";

/// @title SyntheticBond
/// @notice ERC-4626 Vault that creates a "Fixed Yield" position using RLD.
/// @dev Strategy: Supply USDC -> Mint RLD (Short Rate) -> Sell RLD via V4 Pool -> Supply USDC.
contract SyntheticBond {
    using SafeTransferLib for ERC20;
    using PoolIdLibrary for PoolKey;
    using CurrencyLibrary for Currency;
    using SafeCast for uint256;
    using SafeCast for int256;

    IRLDCore public immutable CORE;
    MarketId public immutable MARKET_ID;
    address public immutable ADAPTER;
    address public immutable ASSET; // USDC
    address public immutable COLLATERAL; // aUSDC
    address public immutable POSITION_TOKEN; // wRLP
    
    // Uniswap V4
    IPoolManager public immutable poolManager;
    PoolKey public poolKey;

    // Checkpoints for accounting
    uint256 public totalShares;
    
    constructor(
        address core, 
        MarketId marketId, 
        address adapter, 
        address asset,
        address collateral,
        address positionToken,
        address _poolManager,
        PoolKey memory _poolKey
    ) {
        CORE = IRLDCore(core);
        MARKET_ID = marketId;
        ADAPTER = adapter;
        ASSET = asset;
        COLLATERAL = collateral;
        POSITION_TOKEN = positionToken;
        poolManager = IPoolManager(_poolManager);
        poolKey = _poolKey;
    }

    // --- Core Logic ---
    // User deposits USDC.
    // 1. Vault supplies to Aave -> Gets aUSDC.
    // 2. Vault flashtrades (Mint RLD -> Sell -> Supply).
    
    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        // 1. Transfer Assets
        ERC20(ASSET).safeTransferFrom(msg.sender, address(this), assets);
        
        // 2. Supply Initial to Aave
        ERC20(ASSET).approve(ADAPTER, assets);
        ILendingAdapter(ADAPTER).supply(ASSET, assets);

        // 3. Execute Strategy via Flash Lock
        // Encode instructions: DEPOSIT
        bytes memory data = abi.encode(1, assets);
        CORE.lock(data); 
        
        // 4. Mint Shares (Simple 1:1 for MVP)
        shares = assets; 
        totalShares += shares;
        
        // TODO: Emit event, mint ERC20 shares
    }

    /* ============================================================================================ */
    /*                                     RlD CORE CALLBACK                                        */
    /* ============================================================================================ */

    function lockAcquired(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(CORE)) revert("Unauthorized");
        
        (uint8 action, uint256 amount) = abi.decode(data, (uint8, uint256));
        
        if (action == 1) { // DEPOSIT
            _stratDeposit(amount);
        }
        return "";
    }
    
    function _stratDeposit(uint256 /*amount*/) internal {
        // 1. Determine Hedge Size
        // MVP: Just mint fixed amount or ratio. 
        // Let's mint 10% of notional for demonstration.
        int256 mintAmount = 10e18; // 10 wRLP
        
        // 2. Post Collateral & Mint Debt
        // Collateral is all aUSDC held by this Vault (from initial deposit)
        uint256 collateralBalance = ERC20(COLLATERAL).balanceOf(address(this));
        ERC20(COLLATERAL).approve(address(CORE), collateralBalance);
        
        CORE.modifyPosition(MARKET_ID, int256(collateralBalance), mintAmount);
        
        // Now Vault holds minted wRLP
        
        // 3. Swap wRLP -> USDC on Uniswap V4
        // We need to enter PoolManager lock to swap?
        // No, swap requires `unlock` call if not already unlocked.
        // We are NOT in V4 lock, strictly speaking. We are in RLD lock.
        // So we call `poolManager.unlock(data)`.
        
        bytes memory swapData = abi.encode(mintAmount); // Amount to sell
        poolManager.unlock(swapData);
        
        // returns here after swap completed and settled in callback
        
        // 4. Loop: Supply proceeds (USDC) to Aave
        uint256 usdcBalance = ERC20(ASSET).balanceOf(address(this));
        if (usdcBalance > 0) {
            ERC20(ASSET).approve(ADAPTER, usdcBalance);
            ILendingAdapter(ADAPTER).supply(ASSET, usdcBalance);
            
            // Post new aUSDC as collateral
            uint256 newCollateral = ERC20(COLLATERAL).balanceOf(address(this));
            if (newCollateral > 0) {
                ERC20(COLLATERAL).approve(address(CORE), newCollateral);
                CORE.modifyPosition(MARKET_ID, int256(newCollateral), 0);
            }
        }
    }

    /* ============================================================================================ */
    /*                                   UNISWAP V4 CALLBACK                                        */
    /* ============================================================================================ */

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(poolManager)) revert("Unauthorized");
        
        // Perform Swap
        int256 amountToSell = abi.decode(data, (int256));
        
        // Exact Input Swap: Sell 'amountToSell' wRLP for USDC
        // zeroForOne depend on token sort order.
        bool zeroForOne = poolKey.currency0 == Currency.wrap(POSITION_TOKEN);
        
        IPoolManager.SwapParams memory params = IPoolManager.SwapParams({
            zeroForOne: zeroForOne,
            amountSpecified: -amountToSell, // Negative = Exact Input
            sqrtPriceLimitX96: zeroForOne ? MIN_PRICE_LIMIT : MAX_PRICE_LIMIT // No limit
        });

        PoolKey memory key = poolKey; // Stack local
        
        BalanceDelta delta = poolManager.swap(key, params, "");
        
        // Settle Delta
        // We PAY the amountSpecified (Input).
        // We TAKE the output.
        
        if (zeroForOne) {
            // We pay Currency0 (wRLP), We take Currency1 (USDC)
            // Delta amount0 is positive (pool receives). Wait, standard is:
            // Delta > 0: Pool owes to User (User takes)
            // Delta < 0: User owes to Pool (User pays)
            // amountSpecified (Input) is negative.
            // So if I specify -10 input. 
            // Delta amount0 should be -10 (I owe 10).
            // Delta amount1 should be +Output (Pool owes me).
            
            // Pay wRLP
            if (delta.amount0() < 0) {
                 ERC20(Currency.unwrap(key.currency0)).safeTransfer(address(poolManager), uint256(uint128(-delta.amount0())));
                 poolManager.settle();
            }
            
            // Take USDC
            if (delta.amount1() > 0) {
                poolManager.take(key.currency1, address(this), uint256(uint128(delta.amount1())));
            }
            
        } else {
             // We pay Currency1 (wRLP), We take Currency0 (USDC)
             if (delta.amount1() < 0) {
                 ERC20(Currency.unwrap(key.currency1)).safeTransfer(address(poolManager), uint256(uint128(-delta.amount1())));
                 poolManager.settle();
            }
            
            if (delta.amount0() > 0) {
                poolManager.take(key.currency0, address(this), uint256(uint128(delta.amount0())));
            }
        }
        
        return "";
    }
    
    // Limits
    uint160 internal constant MIN_PRICE_LIMIT = 4295128739;
    uint160 internal constant MAX_PRICE_LIMIT = 1461446703485210103287273052203988822378723970342;
}
