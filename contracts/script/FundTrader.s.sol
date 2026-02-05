// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";

interface IPool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

interface IWrappedAToken {
    function wrap(uint256 aTokenAmount) external returns (uint256 shares);
    function aToken() external view returns (address);
}

/**
 * @title FundTrader
 * @notice Funds the trader wallet with waUSDC for chaotic trading
 * @dev Flow: USDC whale → Aave deposit → aUSDC → wrap() → waUSDC
 */
contract FundTrader is Script {
    // Mainnet addresses
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant USDC_WHALE = 0xCFFAd3200574698b78f32232aa9D63eABD290703;
    
    // Amount to fund (10M waUSDC)
    uint256 constant FUND_AMOUNT = 10_000_000e6;

    function run() external {
        address waUSDC = vm.envAddress("WAUSDC");
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        
        console.log("=== FUND TRADER SCRIPT ===");
        console.log("Deployer:", deployer);
        console.log("waUSDC:", waUSDC);
        console.log("Fund amount:", FUND_AMOUNT / 1e6, "tokens");
        console.log("");
        
        // Check whale balance
        uint256 whaleBalance = ERC20(USDC).balanceOf(USDC_WHALE);
        console.log("Whale USDC balance:", whaleBalance / 1e6);
        require(whaleBalance >= FUND_AMOUNT, "Whale insufficient balance");
        
        // ─── STEP 1: Impersonate whale and deposit USDC to Aave ───
        console.log("");
        console.log("STEP 1: Depositing USDC to Aave...");
        
        vm.startPrank(USDC_WHALE);
        ERC20(USDC).approve(AAVE_POOL, FUND_AMOUNT);
        IPool(AAVE_POOL).supply(USDC, FUND_AMOUNT, deployer, 0);
        vm.stopPrank();
        
        uint256 aUsdcBalance = ERC20(AUSDC).balanceOf(deployer);
        console.log("Deployer aUSDC balance:", aUsdcBalance / 1e6);
        
        // ─── STEP 2: Wrap aUSDC to waUSDC using wrap() ───
        console.log("");
        console.log("STEP 2: Wrapping aUSDC to waUSDC...");
        
        vm.startBroadcast(deployerKey);
        
        // Approve waUSDC wrapper to spend aUSDC
        ERC20(AUSDC).approve(waUSDC, aUsdcBalance);
        
        // Call wrap() - the RLD WrappedAToken interface
        uint256 waUsdcReceived = IWrappedAToken(waUSDC).wrap(aUsdcBalance);
        
        vm.stopBroadcast();
        
        uint256 waUsdcBalance = ERC20(waUSDC).balanceOf(deployer);
        console.log("Deployer waUSDC balance:", waUsdcBalance / 1e6);
        
        // ─── DONE ───
        console.log("");
        console.log("=== TRADER FUNDED ===");
        console.log("waUSDC received:", waUsdcReceived / 1e6);
        console.log("Ready for chaotic trading!");
    }
}
