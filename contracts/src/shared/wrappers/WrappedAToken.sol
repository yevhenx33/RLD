// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";

/**
 * @title WrappedAToken
 * @notice A wstETH-style wrapper for Aave aTokens that converts rebasing balances
 *         into non-rebasing shares. This enables aToken compatibility with protocols
 *         that don't support rebasing tokens (like Uniswap V4).
 * 
 * @dev The wrapper uses a shares-based accounting system:
 *      - Users deposit aTokens and receive shares (waToken)
 *      - Share balance stays constant; value accrues through exchange rate
 *      - Exchange rate = totalATokens / totalShares
 *
 * Unlike ERC-4626, this implementation is simpler since aTokens don't require
 * explicit yield claiming - the balance automatically increases.
 */
contract WrappedAToken is ERC20 {
    using SafeTransferLib for ERC20;

    /*//////////////////////////////////////////////////////////////
                               IMMUTABLES
    //////////////////////////////////////////////////////////////*/

    /// @notice The underlying aToken being wrapped
    ERC20 public immutable aToken;

    /// @notice Decimals of the underlying aToken
    uint8 private immutable _decimals;

    /*//////////////////////////////////////////////////////////////
                              CONSTRUCTOR
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Creates a new wrapped aToken
     * @param _aToken The aToken to wrap (e.g., aUSDC)
     * @param _name Name for the wrapped token (e.g., "Wrapped aUSDC")
     * @param _symbol Symbol for the wrapped token (e.g., "waUSDC")
     */
    constructor(
        address _aToken,
        string memory _name,
        string memory _symbol
    ) ERC20(_name, _symbol, ERC20(_aToken).decimals()) {
        aToken = ERC20(_aToken);
        _decimals = ERC20(_aToken).decimals();
    }

    /*//////////////////////////////////////////////////////////////
                            WRAP / UNWRAP
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Wraps aTokens into waTokens (shares)
     * @param aTokenAmount Amount of aTokens to wrap
     * @return shares Amount of waToken shares received
     */
    function wrap(uint256 aTokenAmount) external returns (uint256 shares) {
        require(aTokenAmount > 0, "Cannot wrap 0");
        
        shares = getSharesByAToken(aTokenAmount);
        require(shares > 0, "Shares cannot be 0");
        
        // Transfer aTokens from sender
        aToken.safeTransferFrom(msg.sender, address(this), aTokenAmount);
        
        // Mint shares to sender
        _mint(msg.sender, shares);
    }

    /**
     * @notice Unwraps waTokens back to aTokens
     * @param shares Amount of waToken shares to burn
     * @return aTokenAmount Amount of aTokens received
     */
    function unwrap(uint256 shares) external returns (uint256 aTokenAmount) {
        require(shares > 0, "Cannot unwrap 0");
        
        aTokenAmount = getATokenByShares(shares);
        require(aTokenAmount > 0, "AToken amount cannot be 0");
        
        // Burn shares from sender
        _burn(msg.sender, shares);
        
        // Transfer aTokens to sender
        aToken.safeTransfer(msg.sender, aTokenAmount);
    }

    /*//////////////////////////////////////////////////////////////
                            VIEW FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Returns total aTokens held by this wrapper
     * @dev This value increases over time as Aave yield accrues
     */
    function totalAssets() public view returns (uint256) {
        return aToken.balanceOf(address(this));
    }

    /**
     * @notice Converts aToken amount to shares
     * @param aTokenAmount Amount of aTokens
     * @return shares Equivalent shares
     */
    function getSharesByAToken(uint256 aTokenAmount) public view returns (uint256 shares) {
        uint256 _totalSupply = totalSupply;
        uint256 _totalAssets = totalAssets();
        
        if (_totalSupply == 0 || _totalAssets == 0) {
            // First deposit: 1:1 ratio
            return aTokenAmount;
        }
        
        // shares = aTokenAmount * totalShares / totalATokens
        return (aTokenAmount * _totalSupply) / _totalAssets;
    }

    /**
     * @notice Converts shares to aToken amount
     * @param shares Amount of shares
     * @return aTokenAmount Equivalent aTokens
     */
    function getATokenByShares(uint256 shares) public view returns (uint256 aTokenAmount) {
        uint256 _totalSupply = totalSupply;
        
        if (_totalSupply == 0) {
            return 0;
        }
        
        // aTokenAmount = shares * totalATokens / totalShares
        return (shares * totalAssets()) / _totalSupply;
    }

    /**
     * @notice Returns the current exchange rate (aTokens per share)
     * @dev Scaled by 1e18 for precision
     * @return rate Current aToken/share rate (1e18 = 1:1)
     */
    function aTokenPerShare() external view returns (uint256 rate) {
        uint256 _totalSupply = totalSupply;
        
        if (_totalSupply == 0) {
            return 1e18; // Initial rate is 1:1
        }
        
        return (totalAssets() * 1e18) / _totalSupply;
    }

    /**
     * @notice Returns shares per aToken (inverse rate)
     * @dev Scaled by 1e18 for precision
     */
    function sharePerAToken() external view returns (uint256 rate) {
        uint256 _totalAssets = totalAssets();
        
        if (_totalAssets == 0) {
            return 1e18; // Initial rate is 1:1
        }
        
        return (totalSupply * 1e18) / _totalAssets;
    }
}
