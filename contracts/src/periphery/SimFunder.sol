// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/src/utils/SafeTransferLib.sol";

/**
 * @title SimFunder
 * @notice Simulation-only helper that atomically funds a user with waUSDC
 *         in a single transaction: USDC → Aave supply → aUSDC → wrap → waUSDC.
 *
 * @dev Replaces 8 sequential transactions + 7 sleep(1) calls in deploy script.
 *      Designed for Anvil fork use only — not for production.
 *
 *      Usage:
 *        1. Impersonate USDC whale
 *        2. whale.approve(simFunder, amount)
 *        3. whale.call simFunder.fund(user, amount)
 *      OR:
 *        1. Impersonate whale, send USDC to SimFunder
 *        2. Anyone calls simFunder.fund(user, amount) — pulls from own balance
 */
interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

interface IWrappedAToken {
    function wrap(uint256 aTokenAmount) external returns (uint256 shares);
    function getSharesByAToken(uint256 aTokenAmount) external view returns (uint256 shares);
    function getATokenByShares(uint256 shares) external view returns (uint256 aTokenAmount);
}

contract SimFunder {
    using SafeTransferLib for ERC20;

    ERC20 public immutable USDC;
    ERC20 public immutable AUSDC;
    ERC20 public immutable WAUSDC;
    IAavePool public immutable AAVE_POOL;
    address public owner;

    constructor(address usdc, address ausdc, address wausdc, address aavePool) {
        USDC = ERC20(usdc);
        AUSDC = ERC20(ausdc);
        WAUSDC = ERC20(wausdc);
        AAVE_POOL = IAavePool(aavePool);
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    /**
     * @notice Atomically fund a user with waUSDC from USDC.
     * @param user    Recipient of waUSDC
     * @param amount  USDC amount (6 decimals)
     *
     * @dev Caller must have approved this contract for `amount` USDC,
     *      OR this contract must already hold sufficient USDC balance.
     */
    function fund(address user, uint256 amount) external {
        // Fast path: if reserve already holds enough waUSDC, transfer directly.
        // This allows Reth snapshot mode to fund users without relying on live
        // Aave state at runtime.
        uint256 waReserve = WAUSDC.balanceOf(address(this));
        if (waReserve >= amount) {
            WAUSDC.safeTransfer(user, amount);
            return;
        }

        // 1. Pull USDC from caller (if caller has approved us)
        uint256 bal = USDC.balanceOf(address(this));
        if (bal < amount) {
            USDC.safeTransferFrom(msg.sender, address(this), amount - bal);
        }

        // 2. Supply USDC to Aave → receive aUSDC to this contract
        USDC.approve(address(AAVE_POOL), amount);
        AAVE_POOL.supply(address(USDC), amount, address(this), 0);

        // 3. Approve aUSDC to waUSDC wrapper
        uint256 aBalance = AUSDC.balanceOf(address(this));
        AUSDC.approve(address(WAUSDC), aBalance);

        // 4. Wrap aUSDC → waUSDC (minted to this contract as msg.sender of wrap())
        IWrappedAToken(address(WAUSDC)).wrap(aBalance);

        // 5. Transfer all waUSDC to user
        uint256 waBalance = WAUSDC.balanceOf(address(this));
        WAUSDC.safeTransfer(user, waBalance);
    }

    /**
     * @notice Fund multiple users in a single transaction.
     * @param users   Array of recipients
     * @param amounts Array of USDC amounts (6 decimals)
     */
    function fundBatch(address[] calldata users, uint256[] calldata amounts) external {
        require(users.length == amounts.length, "length mismatch");
        for (uint256 i = 0; i < users.length; i++) {
            this.fund(users[i], amounts[i]);
        }
    }

    /**
     * @notice Owner-only reserve top-up path that mints waUSDC from USDC already
     *         held by this contract and keeps the resulting waUSDC inside reserve.
     * @dev Used by faucet in Reth mode to avoid per-request Aave dependency.
     */
    function primeReserve(uint256 amount) external onlyOwner returns (uint256 mintedShares) {
        require(amount > 0, "amount=0");
        uint256 bal = USDC.balanceOf(address(this));
        require(bal >= amount, "Insufficient USDC reserve");

        USDC.approve(address(AAVE_POOL), amount);
        AAVE_POOL.supply(address(USDC), amount, address(this), 0);

        uint256 aBalance = AUSDC.balanceOf(address(this));
        AUSDC.approve(address(WAUSDC), aBalance);
        mintedShares = IWrappedAToken(address(WAUSDC)).wrap(aBalance);
    }

    /**
     * @notice Owner-only direct reserve transfer path.
     */
    function transferReserve(address user, uint256 amount) external onlyOwner {
        WAUSDC.safeTransfer(user, amount);
    }

    /**
     * @notice View helper for off-chain health checks.
     */
    function availableWAUSDC() external view returns (uint256) {
        return WAUSDC.balanceOf(address(this));
    }

    /**
     * @notice View helper: estimate waUSDC shares for given USDC amount.
     * @dev Assumes 1 aUSDC minted per 1 USDC supplied at 6 decimals.
     */
    function previewSharesFromUSDC(uint256 usdcAmount) external view returns (uint256 shares) {
        shares = IWrappedAToken(address(WAUSDC)).getSharesByAToken(usdcAmount);
    }
}
