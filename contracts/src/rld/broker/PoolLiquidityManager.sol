// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";

/// @dev Minimal ERC721 for ownership check
interface IERC721Minimal {
    function ownerOf(uint256 tokenId) external view returns (address);
    function setApprovalForAll(address operator, bool approved) external;
}

/// @dev Minimal Permit2 interface for token approvals
interface IPermit2 {
    function approve(
        address token,
        address spender,
        uint160 amount,
        uint48 expiration
    ) external;
}

/// @dev Minimal ERC20 interface for approvals
interface IERC20Minimal {
    function approve(address spender, uint256 amount) external returns (bool);
}

/// @title PoolLiquidityManager — Standalone LP functions for isolated testing
/// @notice Extracted from PrimeBroker. Contains addPoolLiquidity, removePoolLiquidity,
///         and the shared _decreaseV4Liquidity helper.
///         This contract can be deployed standalone for unit testing the V4 LP logic
///         without compiling the entire PrimeBroker dependency chain.
contract PoolLiquidityManager {
    /* ═══════════════════════════════════════════════════════════════ */
    /*                          STORAGE                               */
    /* ═══════════════════════════════════════════════════════════════ */

    address public immutable POSM;
    address public constant PERMIT2 =
        0x000000000022D473030F116dDEE9F6B43aC78BA3;

    address public positionToken; // wRLP
    address public collateralToken; // waUSDC
    address public hookAddress;
    uint256 public activeTokenId;
    address public owner;

    /* ═══════════════════════════════════════════════════════════════ */
    /*                          EVENTS                                */
    /* ═══════════════════════════════════════════════════════════════ */

    event LiquidityAdded(uint256 indexed tokenId, uint128 liquidity);
    event LiquidityRemoved(
        uint256 indexed tokenId,
        uint128 liquidity,
        bool burned
    );
    event ActivePositionChanged(uint256 oldTokenId, uint256 newTokenId);

    /* ═══════════════════════════════════════════════════════════════ */
    /*                        CONSTRUCTOR                             */
    /* ═══════════════════════════════════════════════════════════════ */

    constructor(
        address _posm,
        address _positionToken,
        address _collateralToken
    ) {
        POSM = _posm;
        positionToken = _positionToken;
        collateralToken = _collateralToken;
        owner = msg.sender;

        // Pre-approve tokens → Permit2 → PositionManager
        IERC20Minimal(_positionToken).approve(PERMIT2, type(uint256).max);
        IERC20Minimal(_collateralToken).approve(PERMIT2, type(uint256).max);
        IPermit2(PERMIT2).approve(
            _positionToken,
            _posm,
            type(uint160).max,
            type(uint48).max
        );
        IPermit2(PERMIT2).approve(
            _collateralToken,
            _posm,
            type(uint160).max,
            type(uint48).max
        );

        // Approve POSM to manage our NFTs (needed for BURN_POSITION)
        IERC721Minimal(_posm).setApprovalForAll(_posm, true);
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    /// @dev Required for receiving V4 LP position NFTs from PositionManager
    function onERC721Received(
        address,
        address,
        uint256,
        bytes calldata
    ) external pure returns (bytes4) {
        return this.onERC721Received.selector;
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                     ADD POOL LIQUIDITY                         */
    /* ═══════════════════════════════════════════════════════════════ */

    /// @notice Adds liquidity to the V4 pool, minting a new LP position NFT
    /// @param twammHook The TWAMM hook address (used to build PoolKey)
    /// @param tickLower Lower tick bound (aligned to tick spacing = 5)
    /// @param tickUpper Upper tick bound (aligned to tick spacing = 5)
    /// @param liquidity Amount of liquidity to add
    /// @param amount0Max Maximum currency0 (slippage protection)
    /// @param amount1Max Maximum currency1 (slippage protection)
    /// @return tokenId The newly minted V4 LP position NFT ID
    function addPoolLiquidity(
        address twammHook,
        int24 tickLower,
        int24 tickUpper,
        uint128 liquidity,
        uint128 amount0Max,
        uint128 amount1Max
    ) external onlyOwner returns (uint256 tokenId) {
        require(liquidity > 0, "Zero liquidity");

        PoolKey memory poolKey = _getPoolKey(twammHook);

        // Actions: MINT_POSITION (0x00) + CLOSE_CURRENCY (0x10) × 2
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
            address(this), // recipient = this contract
            bytes("") // no hook data
        );
        params[1] = abi.encode(poolKey.currency0);
        params[2] = abi.encode(poolKey.currency1);

        IPositionManager(POSM).modifyLiquidities(
            abi.encode(actions, params),
            block.timestamp + 60
        );

        // Auto-track first position only
        tokenId = IPositionManager(POSM).nextTokenId() - 1;
        if (activeTokenId == 0) {
            activeTokenId = tokenId;
            emit ActivePositionChanged(0, tokenId);
        }

        // Cache hook address
        if (hookAddress == address(0)) {
            hookAddress = twammHook;
        }

        emit LiquidityAdded(tokenId, liquidity);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                    REMOVE POOL LIQUIDITY                       */
    /* ═══════════════════════════════════════════════════════════════ */

    /// @notice Removes liquidity from a specific V4 LP position
    /// @param tokenId  The V4 LP position NFT ID
    /// @param liquidity Amount of liquidity to remove (capped if exceeds current)
    function removePoolLiquidity(
        uint256 tokenId,
        uint128 liquidity
    ) external onlyOwner {
        require(liquidity > 0, "Zero liquidity");
        require(
            IERC721Minimal(POSM).ownerOf(tokenId) == address(this),
            "Not position owner"
        );

        uint128 currentLiquidity = IPositionManager(POSM).getPositionLiquidity(
            tokenId
        );
        require(currentLiquidity > 0, "Empty position");

        bool fullRemoval = liquidity >= currentLiquidity;
        uint128 actualLiquidity = fullRemoval ? currentLiquidity : liquidity;

        // Use shared helper
        _decreaseV4Liquidity(tokenId, actualLiquidity, fullRemoval);

        // Clear tracking if this was the tracked position and fully removed
        if (fullRemoval && tokenId == activeTokenId) {
            uint256 old = activeTokenId;
            activeTokenId = 0;
            emit ActivePositionChanged(old, 0);
        }

        emit LiquidityRemoved(tokenId, actualLiquidity, fullRemoval);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                     SET ACTIVE POSITION                        */
    /* ═══════════════════════════════════════════════════════════════ */

    function setActiveV4Position(uint256 newTokenId) external onlyOwner {
        if (newTokenId != 0) {
            require(
                IERC721Minimal(POSM).ownerOf(newTokenId) == address(this),
                "Not position owner"
            );
        }
        uint256 old = activeTokenId;
        activeTokenId = newTokenId;
        emit ActivePositionChanged(old, newTokenId);
    }

    /* ═══════════════════════════════════════════════════════════════ */
    /*                     INTERNAL HELPERS                           */
    /* ═══════════════════════════════════════════════════════════════ */

    /// @dev Shared helper — reused by removePoolLiquidity and _unwindV4Position (in PrimeBroker)
    function _decreaseV4Liquidity(
        uint256 tokenId,
        uint128 liquidity,
        bool burn
    ) internal {
        (PoolKey memory pk, ) = IPositionManager(POSM).getPoolAndPositionInfo(
            tokenId
        );

        if (burn) {
            // BURN_POSITION handles decreasing to 0 internally, then burns NFT
            // We just need BURN + TAKE_PAIR to collect tokens
            bytes memory actions = abi.encodePacked(
                uint8(0x03), // BURN_POSITION (handles decrease + burn)
                uint8(0x11) // TAKE_PAIR
            );

            bytes[] memory params = new bytes[](2);
            params[0] = abi.encode(tokenId, uint128(0), uint128(0), bytes(""));
            params[1] = abi.encode(pk.currency0, pk.currency1, address(this));

            IPositionManager(POSM).modifyLiquidities(
                abi.encode(actions, params),
                block.timestamp + 60
            );
        } else {
            // DECREASE_LIQUIDITY + TAKE_PAIR (keep position)
            bytes memory actions = abi.encodePacked(uint8(0x01), uint8(0x11));

            bytes[] memory params = new bytes[](2);
            params[0] = abi.encode(
                tokenId,
                liquidity,
                uint128(0),
                uint128(0),
                bytes("")
            );
            params[1] = abi.encode(pk.currency0, pk.currency1, address(this));

            IPositionManager(POSM).modifyLiquidities(
                abi.encode(actions, params),
                block.timestamp + 60
            );
        }
    }

    /// @dev Builds PoolKey with sorted currencies
    function _getPoolKey(
        address twammHook
    ) internal view virtual returns (PoolKey memory) {
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
}
