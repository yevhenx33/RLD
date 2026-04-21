// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IPrimeBroker} from "../../shared/interfaces/IPrimeBroker.sol";
import {IRLDCore, MarketId} from "../../shared/interfaces/IRLDCore.sol";
import {IValuationModule} from "../../shared/interfaces/IValuationModule.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {FixedPointMathLib} from "../../shared/utils/FixedPointMathLib.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {ITwapEngine} from "../../dex/interfaces/ITwapEngine.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";

/// @dev Minimal ERC721 interface for ownership checks
interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
}

/// @title PrimeBrokerOpsModule
/// @notice Delegatecall module that hosts heavy V4/TWAMM/operator-signature logic.
/// @dev Storage layout MUST match PrimeBroker from slot 0 onward.
contract PrimeBrokerOpsModule {
    using SafeTransferLib for ERC20;
    using FixedPointMathLib for uint256;

    /* ============================================================================================ */
    /*                            STORAGE LAYOUT (MUST MATCH BROKER)                                */
    /* ============================================================================================ */

    // Slot 0 reserved for ReentrancyGuard._status in PrimeBroker.
    // Keep this placeholder to preserve exact delegatecall storage alignment.
    uint256 private _status;
    address public CORE;
    address public factory;
    MarketId public marketId;
    address public collateralToken;
    address public underlyingToken;
    address public positionToken;
    address public underlyingPool;
    address public rateOracle;
    address public settlementModule;
    address public twapEngine;

    uint48 public constant WITHDRAWAL_DELAY = 7 days;
    uint256 public nextWithdrawalId;
    uint256 public queuedCollateralTotal;
    uint64 public withdrawalQueueEpoch;
    mapping(uint256 withdrawalId => IPrimeBroker.WithdrawalRequest request)
        internal withdrawalRequests;

    uint256 public activeTokenId;
    IPrimeBroker.TwammOrderInfo public activeTwammOrder;

    uint8 public constant MAX_OPERATORS = 8;
    mapping(address => bool) public operators;
    address[] public operatorList;
    bool internal initialized;
    mapping(address => uint256) public operatorNonces;
    bool public frozen;

    /* ============================================================================================ */
    /*                                            EVENTS                                             */
    /* ============================================================================================ */

    event OperatorUpdated(address indexed operator, bool active);
    event LiquidityAdded(uint256 indexed tokenId, uint128 liquidity);
    event LiquidityRemoved(uint256 indexed tokenId, uint128 liquidity, bool burned);
    event ActivePositionChanged(uint256 oldTokenId, uint256 newTokenId);
    event TwammOrderSubmitted(
        bytes32 indexed orderId,
        bool zeroForOne,
        uint256 amountIn,
        uint256 expiration
    );
    event TwammOrderCancelled(
        bytes32 indexed orderId,
        uint256 buyTokensOut,
        uint256 sellTokensRefund
    );
    event TwammOrderClaimed(
        bytes32 indexed orderId,
        uint256 claimed0,
        uint256 claimed1
    );
    event ActiveTwammOrderChanged(bytes32 oldOrderId, bytes32 newOrderId);

    /* ============================================================================================ */
    /*                                             ERRORS                                            */
    /* ============================================================================================ */

    error Insolvent();
    error NotOwner();
    error TooManyOperators();
    error NoActivePosition();
    error ZeroLiquidity();
    error InvalidOrder();
    error NoActiveOrder();
    error DuplicateOperator();
    error NotOperator();
    error InvalidNonce();
    error InvalidSignature();

    /* ============================================================================================ */
    /*                                      EXTERNAL ENTRYPOINTS                                     */
    /* ============================================================================================ */

    function collectV4Fees(address posm) external {
        if (activeTokenId == 0) revert NoActivePosition();
        if (IERC721(posm).ownerOf(activeTokenId) != address(this)) revert NotOwner();
        _decreaseV4Liquidity(posm, activeTokenId, 0, false);
    }

    function setActiveV4Position(address posm, uint256 newTokenId) external {
        if (newTokenId != 0) {
            if (IERC721(posm).ownerOf(newTokenId) != address(this)) revert NotOwner();
        }

        uint256 oldTokenId = activeTokenId;
        activeTokenId = newTokenId;
        emit ActivePositionChanged(oldTokenId, newTokenId);
        _checkSolvency();
    }

    function addPoolLiquidity(
        address posm,
        address twammHook,
        int24 tickLower,
        int24 tickUpper,
        uint128 liquidity,
        uint128 amount0Max,
        uint128 amount1Max
    ) external returns (uint256 tokenId) {
        if (liquidity == 0) revert ZeroLiquidity();

        PoolKey memory poolKey = _getPoolKey(twammHook);

        bytes memory actions = abi.encodePacked(
            uint8(0x02), // MINT_POSITION
            uint8(0x12), // CLOSE_CURRENCY
            uint8(0x12) // CLOSE_CURRENCY
        );

        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(
            poolKey,
            tickLower,
            tickUpper,
            liquidity,
            amount0Max,
            amount1Max,
            address(this),
            bytes("")
        );
        params[1] = abi.encode(poolKey.currency0);
        params[2] = abi.encode(poolKey.currency1);

        IPositionManager(posm).modifyLiquidities(
            abi.encode(actions, params),
            block.timestamp + 60
        );

        tokenId = IPositionManager(posm).nextTokenId() - 1;
        if (activeTokenId == 0) {
            uint256 oldTokenId = activeTokenId;
            activeTokenId = tokenId;
            emit ActivePositionChanged(oldTokenId, tokenId);
        }

        emit LiquidityAdded(tokenId, liquidity);
        _checkSolvency();
    }

    function removePoolLiquidity(
        address posm,
        uint256 tokenId,
        uint128 liquidity
    ) external returns (uint256 amount0, uint256 amount1) {
        if (liquidity == 0) revert ZeroLiquidity();
        if (IERC721(posm).ownerOf(tokenId) != address(this)) revert NotOwner();

        uint128 currentLiquidity = IPositionManager(posm).getPositionLiquidity(
            tokenId
        );
        if (currentLiquidity == 0) revert ZeroLiquidity();

        bool fullRemoval = liquidity >= currentLiquidity;
        uint128 actualLiquidity = fullRemoval ? currentLiquidity : liquidity;

        _decreaseV4Liquidity(posm, tokenId, actualLiquidity, fullRemoval);

        if (fullRemoval && tokenId == activeTokenId) {
            emit ActivePositionChanged(activeTokenId, 0);
            activeTokenId = 0;
        }

        emit LiquidityRemoved(tokenId, actualLiquidity, fullRemoval);
        _checkSolvency();
    }

    function setActiveTwammOrder(
        address _twapEngine,
        IPrimeBroker.TwammOrderInfo calldata info
    ) external {
        if (info.orderId != bytes32(0)) {
            (address owner, uint256 sellRate, , , , ) = ITwapEngine(_twapEngine)
                .streamOrders(info.marketId, info.orderId);

            if (sellRate == 0) revert InvalidOrder();
            if (owner != address(this)) revert NotOwner();
        }

        bytes32 oldOrderId = activeTwammOrder.orderId;
        activeTwammOrder = info;
        twapEngine = _twapEngine;
        emit ActiveTwammOrderChanged(oldOrderId, info.orderId);
        _checkSolvency();
    }

    function clearActiveV4Position() external {
        uint256 oldTokenId = activeTokenId;
        activeTokenId = 0;
        emit ActivePositionChanged(oldTokenId, 0);
        _checkSolvency();
    }

    function submitTwammOrder(
        address _twapEngine,
        bytes32 _marketId,
        bool zeroForOne,
        uint256 duration,
        uint256 amountIn
    ) external returns (bytes32 orderId) {
        twapEngine = _twapEngine;

        (address c0, address c1) = positionToken < collateralToken
            ? (positionToken, collateralToken)
            : (collateralToken, positionToken);
        address sellToken = zeroForOne ? c0 : c1;

        address ghostRouter = ITwapEngine(_twapEngine).ghostRouter();
        ERC20(sellToken).approve(ghostRouter, amountIn);
        orderId = ITwapEngine(_twapEngine).submitStream(
            _marketId,
            zeroForOne,
            duration,
            amountIn
        );
        ERC20(sellToken).approve(ghostRouter, 0);

        bytes32 oldOrderId = activeTwammOrder.orderId;
        activeTwammOrder = IPrimeBroker.TwammOrderInfo({
            marketId: _marketId,
            orderId: orderId
        });
        (, , , , uint256 expiration, ) = ITwapEngine(_twapEngine).streamOrders(
            _marketId,
            orderId
        );
        emit TwammOrderSubmitted(orderId, zeroForOne, amountIn, expiration);
        emit ActiveTwammOrderChanged(oldOrderId, orderId);
        _checkSolvency();
    }

    function cancelTwammOrder()
        external
        returns (uint256 buyTokensOut, uint256 sellTokensRefund)
    {
        if (activeTwammOrder.orderId == bytes32(0)) revert NoActiveOrder();
        bytes32 cancelledOrderId = activeTwammOrder.orderId;
        (buyTokensOut, sellTokensRefund) = _cancelTwammOrder();
        emit TwammOrderCancelled(cancelledOrderId, buyTokensOut, sellTokensRefund);
        emit ActiveTwammOrderChanged(cancelledOrderId, bytes32(0));
        _checkSolvency();
    }

    function claimExpiredTwammOrder()
        external
        returns (uint256 claimedBuyToken)
    {
        if (activeTwammOrder.orderId == bytes32(0)) revert NoActiveOrder();
        bytes32 claimedOrderId = activeTwammOrder.orderId;

        claimedBuyToken = ITwapEngine(twapEngine).claimTokens(
            activeTwammOrder.marketId,
            activeTwammOrder.orderId
        );

        delete activeTwammOrder;
        emit TwammOrderClaimed(claimedOrderId, claimedBuyToken, 0);
        emit ActiveTwammOrderChanged(claimedOrderId, bytes32(0));
    }

    function claimExpiredTwammOrderWithId(
        address _twapEngine,
        bytes32 _marketId,
        bytes32 _orderId
    ) external returns (uint256 claimedBuyToken) {
        (address owner, , , , , ) = ITwapEngine(_twapEngine).streamOrders(
            _marketId,
            _orderId
        );
        if (owner != address(this)) revert NotOwner();

        claimedBuyToken = ITwapEngine(_twapEngine).claimTokens(
            _marketId,
            _orderId
        );
        emit TwammOrderClaimed(_orderId, claimedBuyToken, 0);
    }

    function cancelTwammOrderInternal()
        external
        returns (uint256 buyTokensOut, uint256 sellTokensRefund)
    {
        (buyTokensOut, sellTokensRefund) = _cancelTwammOrder();
    }

    function unwindV4PositionInternal(
        address posm,
        address v4Module,
        uint256 targetAmount
    ) external {
        _unwindV4Position(posm, v4Module, targetAmount);
    }

    function updateOperator(address operator, bool active) external {
        _updateOperator(operator, active);
    }

    function setOperatorWithSignature(
        address operator,
        bool active,
        bytes calldata signature,
        uint256 nonce,
        bytes32 commitment
    ) external {
        address owner = IERC721(factory).ownerOf(uint256(uint160(address(this))));

        if (nonce != operatorNonces[msg.sender]) revert InvalidNonce();
        operatorNonces[msg.sender]++;

        bytes32 structHash = keccak256(
            abi.encode(
                operator,
                active,
                address(this),
                nonce,
                msg.sender,
                commitment,
                block.chainid
            )
        );
        bytes32 ethSignedHash = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash)
        );

        address signer = ECDSA.recover(ethSignedHash, signature);
        if (signer != owner) revert InvalidSignature();

        _updateOperator(operator, active);
    }

    /* ============================================================================================ */
    /*                                       INTERNAL HELPERS                                       */
    /* ============================================================================================ */

    function _cancelTwammOrder()
        internal
        returns (uint256 buyTokensOut, uint256 sellTokensRefund)
    {
        (sellTokensRefund, buyTokensOut) = ITwapEngine(twapEngine).cancelOrder(
            activeTwammOrder.marketId,
            activeTwammOrder.orderId
        );
        delete activeTwammOrder;
    }

    function _unwindV4Position(
        address posm,
        address v4Module,
        uint256 targetAmount
    ) internal {
        bytes memory valData = _encodeModuleData(activeTokenId, posm);
        uint256 totalValue = IValuationModule(v4Module).getValue(valData);
        if (totalValue == 0) return;

        uint128 totalLiquidity = IPositionManager(posm).getPositionLiquidity(
            activeTokenId
        );
        uint128 liquidityToRemove = totalLiquidity;

        if (totalValue > targetAmount) {
            liquidityToRemove = uint128(
                uint256(totalLiquidity).mulDivUp(targetAmount, totalValue)
            );
        }
        if (liquidityToRemove == 0) return;

        bool fullRemoval = liquidityToRemove >= totalLiquidity;
        _decreaseV4Liquidity(posm, activeTokenId, liquidityToRemove, fullRemoval);

        if (fullRemoval) {
            activeTokenId = 0;
        }
    }

    function _decreaseV4Liquidity(
        address posm,
        uint256 tokenId,
        uint128 liquidity,
        bool burn
    ) internal {
        (PoolKey memory pk, ) = IPositionManager(posm).getPoolAndPositionInfo(
            tokenId
        );

        if (burn) {
            bytes memory actions = abi.encodePacked(
                uint8(0x03), // BURN_POSITION
                uint8(0x11) // TAKE_PAIR
            );

            bytes[] memory params = new bytes[](2);
            params[0] = abi.encode(tokenId, uint128(0), uint128(0), bytes(""));
            params[1] = abi.encode(pk.currency0, pk.currency1, address(this));

            IPositionManager(posm).modifyLiquidities(
                abi.encode(actions, params),
                block.timestamp + 60
            );
        } else {
            bytes memory actions = abi.encodePacked(
                uint8(0x01), // DECREASE_LIQUIDITY
                uint8(0x11) // TAKE_PAIR
            );

            bytes[] memory params = new bytes[](2);
            params[0] = abi.encode(
                tokenId,
                liquidity,
                uint128(0),
                uint128(0),
                bytes("")
            );
            params[1] = abi.encode(pk.currency0, pk.currency1, address(this));

            IPositionManager(posm).modifyLiquidities(
                abi.encode(actions, params),
                block.timestamp + 60
            );
        }
    }

    function _getPoolKey(address twammHook) internal view returns (PoolKey memory) {
        (address c0, address c1) = positionToken < collateralToken
            ? (positionToken, collateralToken)
            : (collateralToken, positionToken);

        return
            PoolKey({
                currency0: Currency.wrap(c0),
                currency1: Currency.wrap(c1),
                fee: 500,
                tickSpacing: 5,
                hooks: IHooks(twammHook)
            });
    }

    function _encodeModuleData(
        uint256 id,
        address posm
    ) internal view returns (bytes memory) {
        return
            abi.encode(
                id,
                posm,
                rateOracle,
                collateralToken,
                positionToken,
                underlyingPool,
                underlyingToken
            );
    }

    function _checkSolvency() internal view {
        if (!IRLDCore(CORE).isSolvent(marketId, address(this))) revert Insolvent();
    }

    function _updateOperator(address operator, bool active) internal {
        if (active) {
            if (operators[operator]) revert DuplicateOperator();
            if (operatorList.length >= MAX_OPERATORS) revert TooManyOperators();
            operators[operator] = true;
            operatorList.push(operator);
        } else {
            if (!operators[operator]) revert NotOperator();
            operators[operator] = false;
            for (uint256 i = 0; i < operatorList.length; i++) {
                if (operatorList[i] == operator) {
                    operatorList[i] = operatorList[operatorList.length - 1];
                    operatorList.pop();
                    break;
                }
            }
        }
        emit OperatorUpdated(operator, active);
    }
}
