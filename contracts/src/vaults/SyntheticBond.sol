// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {IERC20} from "../interfaces/IERC20.sol";
import {ILendingAdapter} from "../interfaces/ILendingAdapter.sol";

// Minimal ERC4626 Interface
interface IERC4626 {
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares);
    function totalAssets() external view returns (uint256);
}

/// @title SyntheticBond
/// @notice ERC-4626 Vault that creates a "Fixed Yield" position using RLD.
/// @dev Strategy: Supply USDC -> Mint RLD (Short Rate) -> Sell RLD -> Supply USDC.
contract SyntheticBond {
    IRLDCore public immutable CORE;
    MarketId public immutable MARKET_ID;
    address public immutable ADAPTER;
    address public immutable ASSET; // USDC
    address public immutable COLLATERAL; // aUSDC
    
    // Checkpoints for accounting
    uint256 public totalShares;
    
    constructor(
        address core, 
        MarketId marketId, 
        address adapter, 
        address asset,
        address collateral
    ) {
        CORE = IRLDCore(core);
        MARKET_ID = marketId;
        ADAPTER = adapter;
        ASSET = asset;
        COLLATERAL = collateral;
    }

    // --- Core Logic ---
    // User deposits USDC.
    // 1. Vault supplies to Aave -> Gets aUSDC.
    // 2. Vault flashtrades (Mint RLD -> Sell -> Supply).
    // Note: requires `lock` callback.
    
    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        // 1. Transfer Assets
        IERC20(ASSET).transferFrom(msg.sender, address(this), assets);
        
        // 2. Execute Strategy via Flash Lock
        // Encode instructions: DEPOSIT
        bytes memory data = abi.encode(1, assets);
        CORE.lock(data); // Calls lockAcquired
        
        // 3. Mint Shares
        // shares = amount (simplification for MVP, should be based on NAV)
        shares = assets; 
        totalShares += shares;
        
        // TODO: Emit event, mint ERC20 shares
    }

    /// @notice Callback from RLDCore.lock()
    function lockAcquired(bytes calldata data) external returns (bytes memory) {
        if (msg.sender != address(CORE)) revert("Unauthorized");
        
        (uint8 action, uint256 amount) = abi.decode(data, (uint8, uint256));
        
        if (action == 1) { // DEPOSIT
            _stratDeposit(amount);
        }
        return "";
    }
    
    function _stratDeposit(uint256 amount) internal {
        // 1. Supply initial USDC to Aave via Adapter
        IERC20(ASSET).approve(ADAPTER, amount);
        ILendingAdapter(ADAPTER).supply(ASSET, amount);
        
        // 2. Mint RLD (Short) against this collateral
        // Strategy: Mint RLD (Debt) to get leverage. 
        // Since we cannot "Sell" RLD (No Pool yet), we simply open the debt position.
        // Ideally we would sell RLD for more USDC -> Deposit more Collateral (Loop).
        // For MVP Verification: We just demonstrate opening the position.
        
        int256 mintAmount = 100e18; // Arbitrary 100 RLD debt
        
        // Collateral is aUSDC held by this Vault
        uint256 collateralBalance = IERC20(COLLATERAL).balanceOf(address(this));
        IERC20(COLLATERAL).approve(address(CORE), collateralBalance);
        
        // Core Logic: 
        // modifyPosition(id, +collateral, +debt)
        // This transfers 'collateralBalance' from Vault to RLDCore as collateral.
        // And records 'mintAmount' as new debt.
        CORE.modifyPosition(MARKET_ID, int256(collateralBalance), int256(mintAmount));
        
        // 3. Sell RLD on Uniswap (SKIPPED - NO POOL)
        // TODO: Integrate Uniswap Swap when Pool is ready.
        
        // 4. Supply proceeds (SKIPPED)
    }
}
