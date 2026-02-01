// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {console} from "forge-std/console.sol";

import {IERC20} from "../src/shared/interfaces/IERC20.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";

import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {LiquidityAmounts} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {PositionInfo, PositionInfoLibrary} from "v4-periphery/src/libraries/PositionInfoLibrary.sol";

interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
    function balanceOf(address owner) external view returns (uint256);
    function safeTransferFrom(address from, address to, uint256 tokenId) external;
    function transferFrom(address from, address to, uint256 tokenId) external;
}

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

interface IPermit2 {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

/// @title TestV4LP - Test V4 liquidity provision with wRLP + aUSDC
/// @notice Flow: Mint wRLP via broker → withdraw to deployer → provide V4 LP directly
contract TestV4LP is Script, StdCheats {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;
    using PositionInfoLibrary for PositionInfo;
    
    // Tokens
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address constant WRLP = 0x776d54ec60D1DDE9190E75F14b896cCe2CEaaC6c;
    
    // Aave
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    
    // RLD Infrastructure
    address constant BROKER_FACTORY = 0x87C7685147A150A069628479bEc5748f491B5cA0;
    bytes32 constant MARKET_ID = 0x6b63870a1260fcb989a7459c5e537d9f8a1f76890dd88927f105de03d513b33c;
    
    // Uniswap V4
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant POSM = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    address constant TWAMM = 0x8E894E20a38B89C004E4FF5691553B08e8e52ac0;
    
    // Pool parameters
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;
    int24 constant TICK_LOWER = 6930;   // ~$2
    int24 constant TICK_UPPER = 29960;  // ~$20
    
    function run() external {
        console.log("====== TEST: V4 LP PROVISION ======");
        
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        console.log("Deployer:", deployer);
        
        vm.startBroadcast(deployerKey);
        
        // ============================================================
        // STEP 1: GET aUSDC FROM AAVE
        // ============================================================
        console.log("");
        console.log("Step 1: Getting aUSDC from Aave...");
        deal(USDC, deployer, 100_000 * 1e6);  // 100k USDC
        IERC20(USDC).approve(AAVE_POOL, type(uint256).max);
        IAavePool(AAVE_POOL).supply(USDC, 100_000 * 1e6, deployer, 0);
        
        uint256 aUSDCBal = IERC20(AUSDC).balanceOf(deployer);
        console.log("  Deployer aUSDC:", aUSDCBal / 1e6);
        
        // ============================================================
        // STEP 2: CREATE BROKER AND MINT wRLP
        // ============================================================
        console.log("");
        console.log("Step 2: Creating broker and minting wRLP...");
        
        address broker = PrimeBrokerFactory(BROKER_FACTORY).createBroker(
            keccak256(abi.encode(block.timestamp, deployer, "v4lp"))
        );
        console.log("  Broker:", broker);
        
        // Transfer collateral to broker and mint wRLP
        uint256 collateralAmount = 50_000 * 1e6;  // 50k aUSDC as collateral
        IERC20(AUSDC).transfer(broker, collateralAmount);
        
        // Mint 2000 wRLP (~$10k at $5 each, ~20% LTV on 50k collateral)
        uint256 wRLPToMint = 2_000 * 1e6;
        PrimeBroker(payable(broker)).modifyPosition(
            MARKET_ID,
            int256(collateralAmount),
            int256(wRLPToMint)
        );
        
        console.log("  Broker wRLP minted:", IERC20(WRLP).balanceOf(broker) / 1e6);
        
        // ============================================================
        // STEP 3: WITHDRAW wRLP TO DEPLOYER
        // ============================================================
        console.log("");
        console.log("Step 3: Withdrawing wRLP to deployer...");
        
        // Withdraw wRLP from broker to deployer
        uint256 wRLPForLP = 1_000 * 1e6;  // Use 1000 wRLP for LP
        PrimeBroker(payable(broker)).withdrawPositionToken(deployer, wRLPForLP);
        
        console.log("  Deployer wRLP:", IERC20(WRLP).balanceOf(deployer) / 1e6);
        console.log("  Broker wRLP remaining:", IERC20(WRLP).balanceOf(broker) / 1e6);
        
        // ============================================================
        // STEP 4: PREPARE FOR V4 LP
        // ============================================================
        console.log("");
        console.log("Step 4: Preparing for V4 LP...");
        
        // Amount of aUSDC to pair with wRLP (roughly balanced at current price)
        // wRLP at $5, so 1000 wRLP = $5000 → pair with ~$5000 aUSDC
        uint256 aUSDCForLP = 5_000 * 1e6;
        
        console.log("  wRLP for LP:", wRLPForLP / 1e6);
        console.log("  aUSDC for LP:", aUSDCForLP / 1e6);
        
        // Approve Permit2 for both tokens
        IERC20(WRLP).approve(PERMIT2, type(uint256).max);
        IERC20(AUSDC).approve(PERMIT2, type(uint256).max);
        
        // Approve POSM via Permit2
        IPermit2(PERMIT2).approve(WRLP, POSM, type(uint160).max, type(uint48).max);
        IPermit2(PERMIT2).approve(AUSDC, POSM, type(uint160).max, type(uint48).max);
        
        console.log("  Approvals set");
        
        // ============================================================
        // STEP 5: ADD V4 LIQUIDITY
        // ============================================================
        console.log("");
        console.log("Step 5: Adding V4 liquidity...");
        
        // Build pool key (currency0 < currency1)
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(WRLP),    // 0x776... < 0x98C...
            currency1: Currency.wrap(AUSDC),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(TWAMM)
        });
        
        // Get current sqrt price
        (uint160 sqrtPriceX96,,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        console.log("  Current sqrtPriceX96:", sqrtPriceX96);
        
        // Calculate liquidity from amounts
        uint160 sqrtLower = TickMath.getSqrtPriceAtTick(TICK_LOWER);
        uint160 sqrtUpper = TickMath.getSqrtPriceAtTick(TICK_UPPER);
        
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPriceX96,
            sqrtLower,
            sqrtUpper,
            wRLPForLP,
            aUSDCForLP
        );
        console.log("  Liquidity to add:", liquidity);
        
        // Build POSM actions: MINT_POSITION + SETTLE_PAIR
        bytes memory actions = abi.encodePacked(
            uint8(Actions.MINT_POSITION),
            uint8(Actions.SETTLE_PAIR)
        );
        
        bytes[] memory params = new bytes[](2);
        params[0] = abi.encode(
            poolKey,
            TICK_LOWER,
            TICK_UPPER,
            uint256(liquidity),
            uint128(wRLPForLP * 105 / 100),   // 5% slippage on wRLP
            uint128(aUSDCForLP * 105 / 100),  // 5% slippage on aUSDC
            deployer,  // recipient of NFT
            bytes("")  // hookData
        );
        params[1] = abi.encode(Currency.wrap(WRLP), Currency.wrap(AUSDC));
        
        bytes memory posmData = abi.encode(actions, params);
        
        // Record balances before
        uint256 wRLPBefore = IERC20(WRLP).balanceOf(deployer);
        uint256 aUSDCBefore = IERC20(AUSDC).balanceOf(deployer);
        
        // Call POSM modifyLiquidities
        IPositionManager(POSM).modifyLiquidities(posmData, block.timestamp + 60);
        
        // ============================================================
        // STEP 6: VERIFY LP POSITION
        // ============================================================
        console.log("");
        console.log("====== LP POSITION CREATED ======");
        
        uint256 wRLPAfter = IERC20(WRLP).balanceOf(deployer);
        uint256 aUSDCAfter = IERC20(AUSDC).balanceOf(deployer);
        
        console.log("  wRLP used:", (wRLPBefore - wRLPAfter) / 1e6);
        console.log("  aUSDC used:", (aUSDCBefore - aUSDCAfter) / 1e6);
        console.log("  Deployer wRLP remaining:", wRLPAfter / 1e6);
        console.log("  Deployer aUSDC remaining:", aUSDCAfter / 1e6);
        
        // Check NFT balance (POSM is ERC721)
        uint256 nftBalance = IERC20(POSM).balanceOf(deployer);
        console.log("  LP NFT count:", nftBalance);
        
        // ============================================================
        // STEP 7: QUERY POOL STATE FROM POOL MANAGER
        // ============================================================
        console.log("");
        console.log("====== POOL STATE ======");
        
        // Get pool liquidity
        uint128 poolLiquidity = IPoolManager(POOL_MANAGER).getLiquidity(poolKey.toId());
        console.log("  Pool total liquidity:", poolLiquidity);
        
        // Get current tick
        (uint160 currentSqrtPrice, int24 currentTick,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        console.log("  Current tick:", currentTick);
        console.log("  Current sqrtPriceX96:", currentSqrtPrice);
        
        // Calculate approximate price from sqrtPriceX96
        // price = (sqrtPriceX96 / 2^96)^2
        // For display: price in token1/token0 terms
        uint256 priceX192 = uint256(currentSqrtPrice) * uint256(currentSqrtPrice);
        uint256 priceNumerator = priceX192 >> 64;  // Divide by 2^64
        uint256 priceDenominator = 1 << 128;       // 2^128
        // Price = priceNumerator / priceDenominator (in aUSDC per wRLP)
        console.log("  Price numerator (x2^128):", priceNumerator);
        
        // ============================================================
        // STEP 8: GET NFT TOKEN ID AND POSITION INFO
        // ============================================================
        console.log("");
        console.log("====== NFT POSITION INFO ======");
        
        // Get the token ID that was minted (last token for this owner)
        uint256 tokenId = IPositionManager(POSM).nextTokenId() - 1;
        console.log("  Token ID:", tokenId);
        
        // Verify owner
        address nftOwner = IERC721(POSM).ownerOf(tokenId);
        console.log("  NFT Owner:", nftOwner);
        require(nftOwner == deployer, "Wrong NFT owner");
        
        // Get pool and position info
        (PoolKey memory posPoolKey, PositionInfo posInfo) = IPositionManager(POSM).getPoolAndPositionInfo(tokenId);
        
        // Decode PositionInfo (it's a packed uint256)
        // PositionInfo packs: tickLower (int24), tickUpper (int24), poolId (bytes25)
        int24 posTickLower = posInfo.tickLower();
        int24 posTickUpper = posInfo.tickUpper();
        uint128 posLiquidity = IPositionManager(POSM).getPositionLiquidity(tokenId);
        
        console.log("  Position tickLower:", posTickLower);
        console.log("  Position tickUpper:", posTickUpper);
        console.log("  Position liquidity:", posLiquidity);
        console.log("  Position currency0:", Currency.unwrap(posPoolKey.currency0));
        console.log("  Position currency1:", Currency.unwrap(posPoolKey.currency1));
        
        // Verify position matches what we created
        require(posTickLower == TICK_LOWER, "Tick lower mismatch");
        require(posTickUpper == TICK_UPPER, "Tick upper mismatch");
        require(posLiquidity > 0, "Position has no liquidity");
        
        // ============================================================
        // STEP 9: GET BROKER NAV BEFORE LP REGISTRATION
        // ============================================================
        console.log("");
        console.log("====== REGISTERING LP AS COLLATERAL ======");
        
        uint256 navBefore = PrimeBroker(payable(broker)).getNetAccountValue();
        console.log("  Broker NAV BEFORE LP registration:", navBefore / 1e6, "(USDC terms)");
        console.log("  Broker still has 1000 wRLP + 50k aUSDC collateral");
        
        // ============================================================
        // STEP 10: TRANSFER LP NFT TO BROKER
        // ============================================================
        console.log("");
        console.log("Step 10: Transferring LP NFT to broker...");
        
        // Transfer the LP NFT from deployer to broker (use transferFrom, not safe variant)
        IERC721(POSM).transferFrom(deployer, broker, tokenId);
        
        // Verify broker now owns the NFT
        address nftOwnerAfter = IERC721(POSM).ownerOf(tokenId);
        console.log("  LP NFT new owner:", nftOwnerAfter);
        require(nftOwnerAfter == broker, "NFT transfer failed");
        
        // ============================================================
        // STEP 11: REGISTER LP POSITION FOR SOLVENCY
        // ============================================================
        console.log("");
        console.log("Step 11: Registering LP position in broker...");
        
        PrimeBroker(payable(broker)).setActiveV4Position(tokenId);
        console.log("  LP position registered with tokenId:", tokenId);
        
        // ============================================================
        // STEP 12: GET BROKER NAV AFTER LP REGISTRATION
        // ============================================================
        console.log("");
        console.log("====== NAV COMPARISON ======");
        
        uint256 navAfter = PrimeBroker(payable(broker)).getNetAccountValue();
        console.log("  Broker NAV AFTER LP registration:", navAfter / 1e6, "(USDC terms)");
        
        uint256 navImprovement = navAfter - navBefore;
        console.log("  NAV improvement from LP:", navImprovement / 1e6, "(USDC terms)");
        
        // Calculate LTV improvement
        // Debt = 2000 wRLP * ~$5 = ~$10,000
        // Original collateral = 50k aUSDC
        // Original LTV = 10k / 50k = 20%
        // New LTV = 10k / (navAfter) 
        
        uint256 debtValue = 2000 * 1e6 * 49 / 10;  // 2000 wRLP * $4.9
        uint256 oldLTV = debtValue * 100 / navBefore;
        uint256 newLTV = debtValue * 100 / navAfter;
        
        console.log("");
        console.log("  Debt value (wRLP):", debtValue / 1e6, "USDC equivalent");
        console.log("  Old LTV:", oldLTV, "%");
        console.log("  New LTV:", newLTV, "%");
        console.log("  LTV improvement:", oldLTV - newLTV, "percentage points");
        
        // Verify NAV actually improved
        require(navAfter > navBefore, "NAV should improve with LP collateral");
        
        vm.stopBroadcast();
        
        console.log("");
        console.log("SUCCESS! LP position registered as collateral, LTV improved!");
    }
}
