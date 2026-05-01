// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PrimeBroker} from "../../rld/broker/PrimeBroker.sol";
import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {ERC721} from "solmate/src/tokens/ERC721.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {
    ISignatureTransfer
} from "permit2/src/interfaces/ISignatureTransfer.sol";
import {PeripheryGhostLib} from "./PeripheryGhostLib.sol";

interface IDepositAdapter {
    function deposit(
        uint256 amount,
        address receiver
    ) external returns (uint256 collateralAmount);
}

library BrokerRouterLib {
    using SafeTransferLib for ERC20;

    error NotAuthorized();
    error InvalidBroker();
    error PermitTokenMismatch();
    error SlippageExceeded();

    function authorizeBroker(
        address broker,
        address brokerFactory,
        MarketId marketId,
        address collateralToken,
        address positionToken
    ) internal view {
        PrimeBroker pb = PrimeBroker(payable(broker));
        if (
            pb.factory() != brokerFactory ||
            MarketId.unwrap(pb.marketId()) != MarketId.unwrap(marketId) ||
            pb.collateralToken() != collateralToken ||
            pb.positionToken() != positionToken
        ) revert InvalidBroker();

        address brokerOwner = ERC721(brokerFactory).ownerOf(
            uint256(uint160(broker))
        );
        if (msg.sender != brokerOwner && !pb.operators(msg.sender)) {
            revert NotAuthorized();
        }
    }

    function depositWithPermit(
        address broker,
        uint256 amount,
        uint256 minCollateralOut,
        ISignatureTransfer permit2,
        address underlyingToken,
        address collateralToken,
        address depositAdapter,
        ISignatureTransfer.PermitTransferFrom calldata permit,
        bytes calldata signature
    ) internal returns (uint256 collateralAmount) {
        if (permit.permitted.token != underlyingToken) {
            revert PermitTokenMismatch();
        }

        permit2.permitTransferFrom(
            permit,
            ISignatureTransfer.SignatureTransferDetails({
                to: address(this),
                requestedAmount: amount
            }),
            msg.sender,
            signature
        );

        collateralAmount = convertDeposit(
            broker,
            amount,
            minCollateralOut,
            underlyingToken,
            collateralToken,
            depositAdapter
        );
    }

    function depositWithApproval(
        address broker,
        uint256 amount,
        uint256 minCollateralOut,
        address underlyingToken,
        address collateralToken,
        address depositAdapter
    ) internal returns (uint256 collateralAmount) {
        ERC20(underlyingToken).safeTransferFrom(
            msg.sender,
            address(this),
            amount
        );

        collateralAmount = convertDeposit(
            broker,
            amount,
            minCollateralOut,
            underlyingToken,
            collateralToken,
            depositAdapter
        );
    }

    function swapBrokerExactInput(
        address broker,
        address ghostRouter,
        PoolKey calldata poolKey,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 minAmountOut
    ) internal returns (uint256 amountOut) {
        PrimeBroker(payable(broker)).withdrawToken(
            tokenIn,
            address(this),
            amountIn
        );
        amountOut = PeripheryGhostLib.swapExactInput(
            ghostRouter,
            poolKey,
            tokenIn,
            tokenOut,
            amountIn,
            minAmountOut
        );
        ERC20(tokenOut).safeTransfer(broker, amountOut);
    }

    function executeShort(
        address broker,
        uint256 initialCollateral,
        uint256 targetDebtAmount,
        PoolKey calldata poolKey,
        uint256 minProceeds,
        address ghostRouter,
        MarketId marketId,
        address collateralToken,
        address positionToken
    ) internal returns (uint256 proceeds) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        bytes32 rawMarketId = MarketId.unwrap(marketId);

        pb.modifyPosition(
            rawMarketId,
            int256(initialCollateral),
            int256(targetDebtAmount)
        );
        pb.withdrawToken(positionToken, address(this), targetDebtAmount);

        proceeds = PeripheryGhostLib.swapExactInput(
            ghostRouter,
            poolKey,
            positionToken,
            collateralToken,
            targetDebtAmount,
            minProceeds
        );
        ERC20(collateralToken).safeTransfer(broker, proceeds);

        pb.modifyPosition(rawMarketId, int256(proceeds), int256(0));
    }

    function closeShort(
        address broker,
        uint256 collateralToSpend,
        PoolKey calldata poolKey,
        uint256 minDebtBought,
        address ghostRouter,
        MarketId marketId,
        address collateralToken,
        address positionToken
    ) internal returns (uint256 debtRepaid) {
        PrimeBroker pb = PrimeBroker(payable(broker));
        pb.withdrawToken(collateralToken, address(this), collateralToSpend);
        uint256 debtBought = PeripheryGhostLib.swapExactInput(
            ghostRouter,
            poolKey,
            collateralToken,
            positionToken,
            collateralToSpend,
            minDebtBought
        );
        ERC20(positionToken).safeTransfer(broker, debtBought);

        debtRepaid = finalizeCloseShort(
            pb,
            debtBought,
            marketId,
            collateralToken
        );
    }

    function convertDeposit(
        address broker,
        uint256 amount,
        uint256 minCollateralOut,
        address underlyingToken,
        address collateralToken,
        address depositAdapter
    ) internal returns (uint256 collateralAmount) {
        ERC20 collateral = ERC20(collateralToken);
        uint256 collateralBefore = collateral.balanceOf(broker);

        ERC20(underlyingToken).safeTransfer(depositAdapter, amount);
        IDepositAdapter(depositAdapter).deposit(amount, broker);

        collateralAmount = collateral.balanceOf(broker) - collateralBefore;
        if (collateralAmount < minCollateralOut) revert SlippageExceeded();
    }

    function finalizeCloseShort(
        PrimeBroker pb,
        uint256 debtBought,
        MarketId marketId,
        address collateralToken
    ) internal returns (uint256 debtRepaid) {
        address broker = address(pb);
        bytes32 rawMarketId = MarketId.unwrap(marketId);

        uint128 currentDebt = IRLDCore(pb.CORE())
            .getPosition(pb.marketId(), address(pb))
            .debtPrincipal;
        debtRepaid = debtBought > currentDebt ? currentDebt : debtBought;

        if (debtRepaid > 0) {
            pb.modifyPosition(rawMarketId, int256(0), -int256(debtRepaid));
        }

        uint256 leftover = ERC20(collateralToken).balanceOf(address(this));
        if (leftover > 0) {
            ERC20(collateralToken).safeTransfer(broker, leftover);
        }
    }

    function validatePoolKey(
        PoolKey calldata poolKey,
        address collateral,
        address position
    ) internal pure {
        PeripheryGhostLib.validatePoolKey(poolKey, collateral, position);
    }
}
