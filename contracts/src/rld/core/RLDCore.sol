// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../shared/interfaces/ISpotOracle.sol";
import {IFundingModel} from "../../shared/interfaces/IFundingModel.sol";
import {RLDStorage} from "./RLDStorage.sol";
import {TransientStorage} from "../../shared/libraries/TransientStorage.sol";
import {FixedPointMath} from "../../shared/libraries/FixedPointMath.sol";
import {IERC20} from "../../shared/interfaces/IERC20.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ILiquidationModule} from "../../shared/interfaces/ILiquidationModule.sol";

import {PositionToken} from "../tokens/PositionToken.sol";
import {IBrokerVerifier} from "../../shared/interfaces/IBrokerVerifier.sol";
import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";

/// @title RLD Core Singleton
/// @dev The Hyperstructure managing all RLD Markets.
contract RLDCore is IRLDCore, RLDStorage {
    using FixedPointMath for uint256;
    using SafeTransferLib for ERC20;

    /// @notice The trusted factory allowed to create markets.
    /// @dev Can be set once.
    address public factory;

    /// @notice Sets the factory address. Can only be called once.
    function setFactory(address _factory) external {
        if (factory != address(0)) revert Unauthorized();
        if (_factory == address(0)) revert InvalidParam("Factory");
        factory = _factory;
        emit SecurityUpdate(MarketId.wrap(bytes32(0)), "SetFactory", msg.sender);
    }

    /* ============================================================================================ */
    /*                                        MARKET LOGIC                                          */
    /* ============================================================================================ */

    function createMarket(MarketAddresses calldata addresses, MarketConfig calldata config) external override onlyFactory returns (MarketId) {

        // Validate
        if (addresses.collateralToken == address(0)) revert InvalidParam("Collateral");
        if (addresses.underlyingToken == address(0)) revert InvalidParam("Underlying");
        if (addresses.rateOracle == address(0)) revert InvalidParam("Rate Oracle");
        if (addresses.spotOracle == address(0)) revert InvalidParam("Spot Oracle");
        // markOracle check removed
        if (addresses.fundingModel == address(0)) revert InvalidParam("Funding");
        if (addresses.positionToken == address(0)) revert InvalidParam("Position Token");
        
        // Use addresses to generate ID to ensure uniqueness per config
        MarketId id = MarketId.wrap(keccak256(abi.encode(
            addresses.collateralToken,
            addresses.underlyingToken,
            addresses.underlyingPool // Pool distinguishes markets
        )));

        if (marketAddresses[id].collateralToken != address(0)) revert MarketAlreadyExists();

        marketAddresses[id] = addresses;
        marketConfigs[id] = config;
        marketStates[id] = MarketState({
            normalizationFactor: 1e18, 
            lastUpdateTimestamp: uint48(block.timestamp)
        });

        emit MarketCreated(id, addresses.collateralToken, addresses.underlyingToken, addresses.underlyingPool);
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
        if (!_isLocked()) revert NotLocked();
        _;
    }

    modifier onlyLockHolder() {
        if (msg.sender != _getLockHolder()) revert Unauthorized();
        _;
    }

    modifier onlyFactory() {
        if (msg.sender != factory) revert Unauthorized();
        _;
    }

    modifier onlyCurator(MarketId id) {
        if (msg.sender != marketAddresses[id].curator) revert Unauthorized();
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

    function modifyPosition(MarketId id, int256 deltaCollateral, int256 deltaDebt) external onlyLock onlyLockHolder {
        // Only the lock holder (vault/user) can modify their own position
        // Or specific authorized operators (TODO: Add operator logic later)
        


        // 1. Update Funding (Lazy)
        _applyFunding(id);

        Position storage pos = positions[id][msg.sender];
        MarketAddresses storage addresses = marketAddresses[id];

        // 2. Apply Changes & Transfers
        if (deltaCollateral != 0) {
            uint256 newCollateral = _applyDelta(pos.collateral, deltaCollateral);
            pos.collateral = uint128(newCollateral);
            
            if (deltaCollateral > 0) {
                // User depositing collateral
                ERC20(addresses.collateralToken).safeTransferFrom(msg.sender, address(this), uint256(deltaCollateral));
            } else {
                // User withdrawing collateral
                // uint256(-delta)
                uint256 amountToWithdraw = uint256(-deltaCollateral);
                ERC20(addresses.collateralToken).safeTransfer(msg.sender, amountToWithdraw);
            }
        }

        if (deltaDebt != 0) {
             uint256 newDebt = _applyDelta(pos.debtPrincipal, deltaDebt);
             pos.debtPrincipal = uint128(newDebt);
        }

        // 3.5 Tokenize Debt (WrappedRLP)
        if (deltaDebt > 0) {
                // Mint (Short Position)
                PositionToken(addresses.positionToken).mint(msg.sender, uint256(deltaDebt));
            } else if (deltaDebt < 0) {
                // Burn (Repay Position)
                PositionToken(addresses.positionToken).burn(msg.sender, uint256(-deltaDebt));
            }

        bytes32 actionKey = keccak256(abi.encode(id, msg.sender, ACTION_SALT));
        uint256 currentType = TransientStorage.tload(actionKey);
        uint256 newType = deltaDebt > 0 ? 2 : 1; 
        
        if (newType > currentType) {
            TransientStorage.tstore(actionKey, newType);
        }
        
        _addTouchedPosition(id, msg.sender);


        emit PositionModified(id, msg.sender, deltaCollateral, deltaDebt);
    }

    /// @notice Iterates through all touched (Market, Account) pairs and verifies solvency.
    function _checkSolvencyOfTouched() internal view {
        uint256 count = TransientStorage.tload(TOUCHED_COUNT_KEY);
        for (uint256 i = 0; i < count; i++) {
            (MarketId id, address user) = _getTouchedPosition(i);
            MarketConfig storage config = marketConfigs[id];
            
            // Retrieve Action Type
            bytes32 actionKey = keccak256(abi.encode(id, user, ACTION_SALT));
            uint256 actionType = TransientStorage.tload(actionKey); // 0 (default) -> Treat as 1, 2 -> Mint
            
            // Use params for ratios
            uint256 requiredRatio = actionType == 2 ? uint256(config.minColRatio) : uint256(config.maintenanceMargin);
            
            _checkSolvency(id, user, requiredRatio);
        }
    }

    /// @notice Checks if a specific user is solvent with a custom ratio.
    function _checkSolvency(MarketId id, address user, uint256 minRatio) internal view {
        if (!_isSolvent(id, user, minRatio)) {
            revert Insolvent(user);
        }
    }

    function _isSolvent(MarketId id, address user, uint256 minRatio) internal view returns (bool) {
        Position memory pos = positions[id][user];
        if (pos.debtPrincipal == 0) return true;

        MarketAddresses storage addresses = marketAddresses[id];
        MarketState memory state = marketStates[id];
        MarketConfig memory config = marketConfigs[id];

        // 1. Verify Broker Status (Strict Enforce)
        // If the user is NOT a valid broker, they are treated as having 0 value against their debt.
        if (config.brokerVerifier == address(0)) return false; // Market must have a verifier
        if (!IBrokerVerifier(config.brokerVerifier).isValidBroker(user)) return false; // User must be a broker

        // 2. Calculate Liabilities (RLD Debt)
        // True Debt = Principal * NormalizationFactor
        uint256 trueDebt = uint256(pos.debtPrincipal).mulWad(state.normalizationFactor);
        
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );
        uint256 debtValue = trueDebt.mulWad(indexPrice);

        // 3. Get Assets (Delegated to Broker)
        // Trust the Broker's reported value (Code is verified)
        uint256 totalValue = IPrimeBroker(user).getNetAccountValue();
                
        // 4. Solvency Equation
        return totalValue >= debtValue.mulWad(minRatio);
    }

    function _applyFunding(MarketId id) internal {
        MarketState storage state = marketStates[id];
        MarketAddresses storage addresses = marketAddresses[id];
        
        // 1. Calculate New State via External Model
        (uint256 newNormFactor, ) = IFundingModel(addresses.fundingModel).calculateFunding(
            MarketId.unwrap(id),
            address(this),
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

    /// @notice Liquidates an insolvent position (Broker-Only).
    function liquidate(MarketId id, address user, uint256 debtToCover) external override {
        _applyFunding(id);

        MarketState storage state = marketStates[id];
        
        MarketAddresses storage addresses = marketAddresses[id];
        MarketConfig storage config = marketConfigs[id];
        
        // 1. Verify Insolvency (Maintenance Margin)
        if (_isSolvent(id, user, uint256(config.maintenanceMargin))) revert UserSolvent(user);
        
        // 2. Verify Broker Status (Strict)
        if (config.brokerVerifier == address(0) || !IBrokerVerifier(config.brokerVerifier).isValidBroker(user)) {
             revert InvalidBroker(user);
        }

        Position storage pos = positions[id][user];
        
        // 3. Liquidation Cap (Configurable Close Factor)
        if (debtToCover > uint256(pos.debtPrincipal).mulWad(uint256(config.liquidationCloseFactor))) revert CloseFactorExceeded();
        
        // 4. Calculate Cost (Debt Value)
        uint256 indexPrice = IRLDOracle(addresses.rateOracle).getIndexPrice(
            addresses.underlyingPool, 
            addresses.underlyingToken
        );
        
        // Cost = DebtToCover * NormFactor * IndexPrice
        uint256 brokerCost = uint256(debtToCover).mulWad(state.normalizationFactor).mulWad(indexPrice);

        // 5. Decrement Debt (Core Accounting)
        pos.debtPrincipal -= uint128(debtToCover);

        // 6. Settle Cost (Liquidator pays)
        // If wRLP is used, we burn it. Else we take underlying.
        if (addresses.positionToken != address(0)) {
                PositionToken(addresses.positionToken).burn(msg.sender, debtToCover);
        } else {
                ERC20(addresses.underlyingToken).safeTransferFrom(msg.sender, address(this), brokerCost);
        }

        // 7. Calculate Seize Amount via Module
        ILiquidationModule.PriceData memory priceData = ILiquidationModule.PriceData({
            indexPrice: indexPrice,
            spotPrice: 1e18, // Assume IndexPrice is in Collateral terms (Relative Price)
            normalizationFactor: state.normalizationFactor
        });

        // Current RLDCore tracks Debt, not Collateral. Collateral is in Broker.
        // We pass 0 for userCollateral/userDebt as the module uses params + priceData for calculation.
        ( , uint256 totalSeized) = ILiquidationModule(addresses.liquidationModule).calculateSeizeAmount(
            debtToCover, 
            0, // userCollateral (not tracked in core)
            0, // userDebt (not tracked in core simply)
            priceData, 
            config, 
            config.liquidationParams
        );
        
        // 8. Seize Assets (Delegated to Broker)
        // Liquidator receives computed value directly from the Broker
        IPrimeBroker(user).seize(totalSeized, msg.sender);
        
        emit PositionModified(id, user, 0, -int256(debtToCover));
    }

    function isSolvent(MarketId id, address user) external view override returns (bool) {
        MarketConfig storage config = marketConfigs[id];
        return _isSolvent(id, user, uint256(config.maintenanceMargin));
    }
}
