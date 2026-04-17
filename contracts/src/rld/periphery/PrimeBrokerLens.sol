// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IValuationModule} from "../../shared/interfaces/IValuationModule.sol";
import {IRLDOracle} from "../../shared/interfaces/IRLDOracle.sol";
import {IJTM} from "../../twamm/IJTM.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";

/// @dev Minimal ERC721 interface for ownership checks
interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
}

/// @dev Internal interface extending IPrimeBroker to expose public getters
interface IPrimeBrokerView is IPrimeBroker {
    function collateralToken() external view returns (address);
    function positionToken() external view returns (address);
    function underlyingToken() external view returns (address);
    function underlyingPool() external view returns (address);
    function rateOracle() external view returns (address);
    function POSM() external view returns (address);
    function V4_MODULE() external view returns (address);
    function TWAMM_MODULE() external view returns (address);
    function CORE() external view returns (address);
    function marketId() external view returns (MarketId);
    function activeTokenId() external view returns (uint256);
    function activeTwammOrder() external view returns (PoolKey memory key, IJTM.OrderKey memory orderKey, bytes32 orderId);
}

/// @title Prime Broker Lens
/// @notice Peripheral contract for indexers and frontends to read broker accounts without bloating core bytecode
contract PrimeBrokerLens {

    /// @notice Emitted for periodic state verification
    event StateAudit(
        address indexed account,
        uint256 collateralBalance,
        uint256 positionBalance,
        uint128 debtPrincipal,
        uint256 nav,
        uint256 blockNumber
    );

    /// @notice Complete broker state for indexing
    struct BrokerState {
        uint256 collateralBalance;
        uint256 positionBalance;
        uint128 debtPrincipal;
        uint256 debtValue;
        uint256 twammSellOwed;
        uint256 twammBuyOwed;
        uint256 v4LPValue;
        uint256 netAccountValue;
        uint256 healthFactor;
        bool isSolvent;
    }

    /// @notice Returns the complete state of a broker for indexing
    function getFullState(address broker) external view returns (BrokerState memory state) {
        IPrimeBrokerView b = IPrimeBrokerView(broker);
        address core = b.CORE();
        MarketId marketId = b.marketId();
        
        // Balances
        state.collateralBalance = ERC20(b.collateralToken()).balanceOf(broker);
        state.positionBalance = ERC20(b.positionToken()).balanceOf(broker);

        // Debt from Core
        IRLDCore.Position memory pos = IRLDCore(core).getPosition(marketId, broker);
        state.debtPrincipal = pos.debtPrincipal;

        // Debt value
        if (state.debtPrincipal > 0) {
            IRLDCore.MarketState memory marketState = IRLDCore(core).getMarketState(marketId);
            uint256 trueDebt = (uint256(state.debtPrincipal) * marketState.normalizationFactor) / 1e18;
            uint256 indexPrice = IRLDOracle(b.rateOracle()).getIndexPrice(b.underlyingPool(), b.underlyingToken());
            state.debtValue = (trueDebt * indexPrice) / 1e18;
        }

        // TWAMM owed amounts
        (PoolKey memory twammKey, IJTM.OrderKey memory twammOrderKey, bytes32 twammOrderId) = b.activeTwammOrder();
        if (twammOrderId != bytes32(0) && twammOrderKey.owner == broker) {
            address twammHook = address(twammKey.hooks);
            (uint256 buyTokensOwed, uint256 sellTokensRefund) = IJTM(twammHook)
                .getCancelOrderState(twammKey, twammOrderKey);
            state.twammSellOwed = sellTokensRefund;
            state.twammBuyOwed = buyTokensOwed;
        }

        // V4 LP value
        uint256 activeTokenId = b.activeTokenId();
        address posm = b.POSM();
        if (activeTokenId != 0 && IERC721(posm).ownerOf(activeTokenId) == broker) {
            bytes memory data = abi.encode(
                activeTokenId,
                posm,
                b.rateOracle(),
                b.collateralToken(),
                b.positionToken(),
                b.underlyingPool(),
                b.underlyingToken()
            );
            state.v4LPValue = IValuationModule(b.V4_MODULE()).getValue(data);
        }

        // NAV
        state.netAccountValue = b.getNetAccountValue();

        // Health factor
        if (state.debtValue > 0) {
            state.healthFactor = (state.netAccountValue * 1e18) / state.debtValue;
            state.isSolvent = IRLDCore(core).isSolvent(marketId, broker);
        } else {
            state.healthFactor = type(uint256).max;
            state.isSolvent = true;
        }
    }

    /// @notice Emits a StateAudit event for reconciliation
    function emitStateAudit(address broker) external {
        IPrimeBrokerView b = IPrimeBrokerView(broker);
        IRLDCore.Position memory pos = IRLDCore(b.CORE()).getPosition(b.marketId(), broker);

        emit StateAudit(
            broker,
            ERC20(b.collateralToken()).balanceOf(broker),
            ERC20(b.positionToken()).balanceOf(broker),
            pos.debtPrincipal,
            b.getNetAccountValue(),
            block.number
        );
    }
}
