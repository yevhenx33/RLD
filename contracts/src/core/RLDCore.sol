// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../interfaces/IRLDCore.sol";
import {IRLDOracle} from "../interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../interfaces/ISpotOracle.sol";
import {IFundingModel} from "../interfaces/IFundingModel.sol";
import {RLDStorage} from "./RLDStorage.sol";
import {TransientStorage} from "../libraries/TransientStorage.sol";
import {FixedPointMath} from "../libraries/FixedPointMath.sol";
import {IERC20} from "../interfaces/IERC20.sol";
import {IRLDHook} from "../interfaces/IRLDHook.sol";
import {ILiquidationModule} from "../interfaces/ILiquidationModule.sol";
import {IDefaultOracle} from "../interfaces/IDefaultOracle.sol";

/// @title RLD Core Singleton
/// @dev The Hyperstructure managing all RLD Markets.
contract RLDCore is IRLDCore, RLDStorage {
    using FixedPointMath for uint256;

    // Actually IRLDCore defines them. RLDCore implements IRLDCore.
    // If we want them in ABI, declaring them here is fine.
    // But `MarketParams` error was in `RLDStorage`.
    // Let's keep events but REMOVE storage mappings.
    
    // --- Events ---


    /* ============================================================================================ */
    /*                                        MARKET LOGIC                                          */
    /* ============================================================================================ */

    function createMarket(MarketAddresses calldata addresses, MarketConfig calldata config) external override returns (MarketId) {
        // Validate
        if (addresses.collateralToken == address(0)) revert("Invalid Collateral");
        if (addresses.underlyingToken == address(0)) revert("Invalid Underlying");
        if (addresses.rateOracle == address(0)) revert("Invalid Rate Oracle");
        if (addresses.spotOracle == address(0)) revert("Invalid Spot Oracle");
        if (addresses.markOracle == address(0)) revert("Invalid Mark Oracle");
        if (addresses.fundingModel == address(0)) revert("Invalid Funding Model");
        
        // Use addresses to generate ID to ensure uniqueness per config
        MarketId id = MarketId.wrap(keccak256(abi.encode(
            addresses.collateralToken,
            addresses.underlyingToken,
            addresses.underlyingPool, // Pool distinguishes markets
            config.marketType
        )));

        if (marketAddresses[id].collateralToken != address(0)) revert("Market Already Exists");

        marketAddresses[id] = addresses;
        marketConfigs[id] = config;
        marketStates[id] = MarketState({
            normalizationFactor: 1e18, 
            lastUpdateTimestamp: uint48(block.timestamp),
            isSettled: false
        });

        emit MarketCreated(id, addresses.collateralToken, addresses.underlyingToken, config.marketType);
        return id;
    }

    function isValidMarket(MarketId id) external view override returns (bool) {
        return marketAddresses[id].collateralToken != address(0);
    }

    function getMarketState(MarketId id) external view returns (MarketState memory) {
        return marketStates[id];
    }

    function getMarketAddresses(MarketId id) external view returns (MarketAddresses memory) {
        return marketAddresses[id];
    }

    function getMarketConfig(MarketId id) external view returns (MarketConfig memory) {
        return marketConfigs[id];
    }

    function getPosition(MarketId id, address user) external view returns (Position memory) {
        return positions[id][user];
    }

    /* ============================================================================================ */
    /*                                      FLASH ACCOUNTING                                        */
    /* ============================================================================================ */

    modifier onlyLock() {
        if (!_isLocked()) revert("Not Locked");
        _;
    }

    function lock(bytes calldata data) external override returns (bytes memory) {
        // 1. Enter Lock
        TransientStorage.tstore(LOCK_HOLDER_KEY, uint256(uint160(msg.sender)));

        // Reset Touched Set
        TransientStorage.tstore(TOUCHED_COUNT_KEY, 0);

        // 2. Callback
        // We call lockAcquired on the caller.
        bytes memory result;
        try IRLDCore(msg.sender).lockAcquired(data) returns (bytes memory res) {
            result = res;
        } catch (bytes memory reason) {
             assembly {
                revert(add(reason, 32), mload(reason))
            }
        }

        // 3. Exit Lock & Check Solvency
        _checkSolvencyOfTouched();
        
        // 4. Cleanup
        TransientStorage.tstore(LOCK_HOLDER_KEY, 0);
        TransientStorage.tstore(TOUCHED_COUNT_KEY, 0);
        
        return result;
    }
    
    // Default callback implementation to satisfy interface, usually called on msg.sender
    function lockAcquired(bytes calldata) external pure returns (bytes memory) {
        revert("Not Implemented by Core");
    }

    /* ============================================================================================ */
    /*                                     POSITION MANAGEMENT                                      */
    /* ============================================================================================ */

    function modifyPosition(MarketId id, int256 deltaCollateral, int256 deltaDebt) external onlyLock {
        // Only the lock holder (vault/user) can modify their own position
        // Or specific authorized operators (TODO: Add operator logic later)
        if (msg.sender != _getLockHolder()) revert("Unauthorized Access");
        
        MarketState storage state = marketStates[id];
        if (state.isSettled) revert("Market Settled");

        // 1. Update Funding (Lazy)
        _applyFunding(id);

        Position storage pos = positions[id][msg.sender];
        MarketAddresses storage addresses = marketAddresses[id];
        MarketConfig storage config = marketConfigs[id];

        // 2. Apply Changes & Transfers
        if (deltaCollateral != 0) {
            uint256 newCollateral = _applyDelta(pos.collateral, deltaCollateral);
            pos.collateral = uint128(newCollateral);
            
            if (deltaCollateral > 0) {
                // User depositing collateral
                // SafeTransferFrom usually recommended, using standard for now
                IERC20(addresses.collateralToken).transferFrom(msg.sender, address(this), uint256(deltaCollateral));
            } else {
                // User withdrawing collateral
                // uint256(-delta)
                uint256 amountToWithdraw = uint256(-deltaCollateral);
                IERC20(addresses.collateralToken).transfer(msg.sender, amountToWithdraw);
            }
        }

        if (deltaDebt != 0) {
             uint256 newDebt = _applyDelta(pos.debtPrincipal, deltaDebt);
             pos.debtPrincipal = uint128(newDebt);
        }

        // 3. Check Hook (CDS Lock)
        // Note: CDSHook logic needs to run *before* transfers technically? 
        // Hook signature: beforeModifyPosition(id, sender, deltaCol, deltaDebt)
        if (addresses.hook != address(0)) {
            // We define a generic interface for Hooks?
            // Let's assume CDSHook interface for now or generic IHook
            // Since we know it's CDSHook:
            // IHook(params.hook).beforeModifyPosition(id, msg.sender, deltaCollateral, deltaDebt);
            // I need to confirm interface linkage. 
            // CDSHook.sol has `beforeModifyPosition`.
            // Casting to an interface needed.
            // Let's rely on low-level call or define IHook interface.
            // For MVP, if hook set, we try to call it?
            // Better: Define IHook in IRLDCore/IHook.
        }



        // 4. Check Hook (CDS Lock)
        if (addresses.hook != address(0)) {
            IRLDHook(addresses.hook).beforeModifyPosition(id, msg.sender, deltaCollateral, deltaDebt);
        }

        // 4. Mark for Solvency Check
        // Track Action Type: If Minting (debt > 0), mark as MINT (Type 2). Else maintain current type (default 1).
        // 0: Unset (should imply 1), 1: Standard (110%), 2: Mint (150%)
        // We use a mapping in TStore? Or encode in touched list?
        // Simpler: Just TStore[keccak(user, id)] = max(current, newType)
        bytes32 actionKey = keccak256(abi.encode(id, msg.sender, "ACTION"));
        uint256 currentType = TransientStorage.tload(actionKey);
        uint256 newType = deltaDebt > 0 ? 2 : 1; 
        
        if (newType > currentType) {
            TransientStorage.tstore(actionKey, newType);
        }
        
        _addTouchedPosition(id, msg.sender);


        emit PositionModified(id, msg.sender, deltaCollateral, deltaDebt);
    }

    /* ============================================================================================ */
    /*                                        SOLVENCY LOGIC                                        */
    /* ============================================================================================ */

    /// @notice Iterates through all touched (Market, Account) pairs and verifies solvency.
    function _checkSolvencyOfTouched() internal view {
        uint256 count = TransientStorage.tload(TOUCHED_COUNT_KEY);
        for (uint256 i = 0; i < count; i++) {
            (MarketId id, address user) = _getTouchedPosition(i);
            MarketConfig storage config = marketConfigs[id];
            
            // Retrieve Action Type
            bytes32 actionKey = keccak256(abi.encode(id, user, "ACTION"));
            uint256 actionType = TransientStorage.tload(actionKey); // 0 (default) -> Treat as 1, 2 -> Mint
            
            // Use params for ratios
            uint256 requiredRatio = actionType == 2 ? uint256(config.minColRatio) : uint256(config.maintenanceMargin);
            
            _checkSolvency(id, user, requiredRatio);
        }
    }

    /// @notice Checks if a specific user is solvent with a custom ratio.
    function _checkSolvency(MarketId id, address user, uint256 minRatio) internal view {
        if (!_isSolvent(id, user, minRatio)) {
            revert("Insolvent");
        }
    }

    function _isSolvent(MarketId id, address user, uint256 minRatio) internal view returns (bool) {
        Position memory pos = positions[id][user];
        if (pos.debtPrincipal == 0) return true;

        MarketAddresses storage addresses = marketAddresses[id];
        MarketState memory state = marketStates[id];

         // True Debt = Principal * NormalizationFactor
        uint256 trueDebt = uint256(pos.debtPrincipal).mulWad(state.normalizationFactor);
        
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );
        
        uint256 debtValue = trueDebt.mulWad(indexPrice);

        uint256 spotPrice = ISpotOracle(addresses.spotOracle).getSpotPrice(
            addresses.collateralToken, 
            addresses.underlyingToken
        );
        
        uint256 collateralValue = uint256(pos.collateral).mulWad(spotPrice);
        
        return collateralValue >= debtValue.mulWad(minRatio);
    }

    function _applyFunding(MarketId id) internal {
        MarketState storage state = marketStates[id];
        MarketAddresses storage addresses = marketAddresses[id];
        
        // 1. Get Prices
        // Note: Mark Price comes from Uniswap V4 (via params.feeHook or separate poolId storage)
        // For MVP, we assume FundingModel handles the Mark Price fetch or we pass it?
        // Architecture Design: FundingModel should be smart enough to fetch Mark if we pass the pool data.
        // Or we pass 0 here and let FundingModel figure it out if it knows the pool.
        // Let's assume we need to pass Mark Price.
        // TODO: Need `params.uniswapPoolId` in MarketParams to fetch Mark Price here via UniswapIntegration.
        // Adding placeholder `0` for markPrice now.
        
        uint256 markPrice = ISpotOracle(addresses.markOracle).getSpotPrice(
            addresses.collateralToken, 
            addresses.underlyingToken
        );
        
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );

        // 2. Calculate New State
        (uint256 newNormFactor, ) = IFundingModel(addresses.fundingModel).calculateFunding(
            markPrice,
            indexPrice,
            state.normalizationFactor,
            state.lastUpdateTimestamp
        );

        // 3. Storage Update
        if (newNormFactor != state.normalizationFactor) {
            state.normalizationFactor = uint128(newNormFactor);
        }
        state.lastUpdateTimestamp = uint48(block.timestamp);
    }

    function _applyDelta(uint128 start, int256 delta) internal pure returns (uint256) {
        int256 result = int256(uint256(start)) + delta;
        if (result < 0) revert("Underflow");
        return uint256(result);
    }

    /* ============================================================================================ */
    /*                                      SETTLEMENT / LIQ                                        */
    /* ============================================================================================ */

    /// @notice Triggers Global Settlement if the Market is Defaulted.
    function settleMarket(MarketId id) external override {
        MarketState storage state = marketStates[id];
        if (state.isSettled) revert("Already Settled");
        
        MarketAddresses storage addresses = marketAddresses[id];
        
        // Check Default Oracle (The "Bank Run" detector)
        if (addresses.defaultOracle != address(0)) {
            bool isDefaulted = IDefaultOracle(addresses.defaultOracle).isDefaulted(
                addresses.underlyingPool, 
                addresses.underlyingToken
            );
            if (!isDefaulted) revert("Not Defaulted");
        } else {
            // If no default oracle, settlement is manual or disabled (revert for safety)
            revert("No Default Oracle");
        }

        // Trigger Settlement
        state.isSettled = true;
        
        // We could snapshot prices here, but simpler to just lock the market state.
        emit MarketSettled(id, 0, 0); 
    }

    /// @notice Liquidates an insolvent position (Legacy/Direct mode).
    function liquidate(MarketId id, address user, uint256 debtToCover) external override {
        _applyFunding(id);

        MarketState storage state = marketStates[id];
        if (state.isSettled) revert("Market Settled");
        
        MarketAddresses storage addresses = marketAddresses[id];
        MarketConfig storage config = marketConfigs[id];
        
        // 1. Verify Insolvency (Maintenance Margin)
        if (_isSolvent(id, user, uint256(config.maintenanceMargin))) revert("User Solvent");
        
        Position storage pos = positions[id][user];
        
        // 1.5 Liquidation Cap (50% Close Factor)
        if (debtToCover > uint256(pos.debtPrincipal) / 2) revert("Close Factor Exceeded");
        
        // 2. Calculate Values
        
        // A. Convert Underlying Amount -> Principal Amount
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );
        
        // Decrement Debt
        pos.debtPrincipal -= uint128(debtToCover);
        
        // B. Calculate Cost + Reward via Module
        uint256 spotPrice = ISpotOracle(addresses.spotOracle).getSpotPrice(
            addresses.collateralToken, 
            addresses.underlyingToken
        );

        ILiquidationModule.PriceData memory priceData = ILiquidationModule.PriceData({
            indexPrice: indexPrice,
            spotPrice: spotPrice,
            normalizationFactor: state.normalizationFactor
        });

        ( , uint256 totalSeized) = ILiquidationModule(addresses.liquidationModule).calculateSeizeAmount(
            debtToCover,
            uint256(pos.collateral),
            uint256(pos.debtPrincipal), // Passed current debt (after decrement? No, logic usually uses debt before? 
                                        // Wait, the module asks for userDebt. Usually HS uses total Debt.
                                        // But here we decremented pos.debtPrincipal already.
                                        // Let's pass (debt + debtToCover) to match original state? 
                                        // Or just pass current state? 
                                        // Module likely needs TOTAL DEBT for HS calculation. 
                                        // So I should pass (pos.debtPrincipal + debtToCover).
            priceData,
            config,
            config.liquidationParams
        );
        
        // Wait, I decremented pos.debtPrincipal at line 400.
        // So `pos.debtPrincipal` is now the remaining debt.
        // For HS check in module, it *might* need original debt, or remaining.
        // Actually, HS check inside module is for Bonus calculation. 
        // HS is usually calculated on the current state. 
        // If I pass the *new* debt, HS is better (higher). 
        // A "Dutch Auction" usually implies the state *at the moment of liquidation availability*.
        // But let's pass (pos.debtPrincipal + debtToCover) as `userDebt` to reflect the state BEFORE this liquidation action 
        // (which determines the depth of insolvency).
        
        uint256 userDebtOriginal = uint256(pos.debtPrincipal) + debtToCover;

        // Re-call with correct debt
         ( , totalSeized) = ILiquidationModule(addresses.liquidationModule).calculateSeizeAmount(
            debtToCover,
            uint256(pos.collateral),
            userDebtOriginal,
            priceData,
            config,
            config.liquidationParams
        );
        
        // C. Calculate Cost in Underlying (to transfer from Liquidator)
        // Cost = Principal * NormFactor * IndexPrice
        uint256 costInUnderlying = uint256(debtToCover).mulWad(state.normalizationFactor).mulWad(indexPrice);

        IERC20(addresses.underlyingToken).transferFrom(msg.sender, address(this), costInUnderlying);
        
        // D. Cap Seize
        if (totalSeized > pos.collateral) {
            totalSeized = pos.collateral; // Cap at max collateral (Bad debt scenario)
        }
        
        pos.collateral -= uint128(totalSeized);
        
        // Transfer Collateral to Liquidator
        IERC20(addresses.collateralToken).transfer(msg.sender, totalSeized);
        
        emit PositionModified(id, user, -int256(totalSeized), -int256(debtToCover));
    }

    function isSolvent(MarketId id, address user) external view override returns (bool) {
        MarketConfig storage config = marketConfigs[id];
        return _isSolvent(id, user, uint256(config.maintenanceMargin));
    }

    function updateRiskParams(MarketId id, uint64 minColRatio, uint64 maintenanceMargin, address liquidationModule, bytes32 liquidationParams) external override {
        MarketAddresses storage addresses = marketAddresses[id];
        if (addresses.collateralToken == address(0)) revert("Invalid Market");
        if (msg.sender != addresses.feeHook) revert("Unauthorized"); // Only Curator
        
        // Input Validation (Basic sanity checks)
        if (maintenanceMargin < 1e18) revert("Unsafe Margin"); // < 100%
        if (minColRatio < maintenanceMargin) revert("Invalid Ratios"); 
        // Bonus validation is now module specific, can't check generic byte32.
        
        MarketConfig storage config = marketConfigs[id];
        config.minColRatio = minColRatio;
        config.maintenanceMargin = maintenanceMargin;
        config.liquidationParams = liquidationParams;
        addresses.liquidationModule = liquidationModule;
    }
}
