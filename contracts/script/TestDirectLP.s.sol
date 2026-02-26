// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {console} from "forge-std/console.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {LiquidityAmounts} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {StateLibrary} from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {FixedPoint96} from "@uniswap/v4-core/src/libraries/FixedPoint96.sol";
import {IRLDCore, MarketId} from "../src/shared/interfaces/IRLDCore.sol";
import {PrimeBroker} from "../src/rld/broker/PrimeBroker.sol";
import {PrimeBrokerFactory} from "../src/rld/core/PrimeBrokerFactory.sol";

interface IPermit2 {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

/// @title DirectLP Test - Add liquidity directly to V4 pool without broker.executeWithApproval
/// @notice This tests LP flow by using deployer as token source instead of broker
contract TestDirectLP is Script, StdCheats {
    using StateLibrary for IPoolManager;

    // V4 Infrastructure
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant POSM = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

    // RLD Infrastructure
    address brokerFactory;
    bytes32 marketId;

    // Tokens
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address WRLP;

    // Aave
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    // Pool parameters
    int24 constant TICK_LOWER = 6930; // ~$2
    int24 constant TICK_UPPER = 29960; // ~$20
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;

    address broker;
    address twamm;

    function run() external {
        console.log("====== DIRECT LP TEST ======");

        string memory json = vm.readFile("./deployments.json");
        twamm = vm.parseJsonAddress(json, ".TWAMM");
        brokerFactory = vm.parseJsonAddress(json, ".BrokerFactory");
        marketId = vm.parseJsonBytes32(json, ".MarketId");
        address coreAddr = vm.parseJsonAddress(json, ".RLDCore");

        // Fetch market addresses to get current WRLP
        IRLDCore.MarketAddresses memory addrs = IRLDCore(coreAddr).getMarketAddresses(MarketId.wrap(marketId));
        WRLP = addrs.positionToken;

        console.log("TWAMM:", twamm);
        console.log("BrokerFactory:", brokerFactory);
        console.log("MarketId:", vm.toString(marketId));
        console.log("WRLP Address:", WRLP);

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        console.log("Deployer:", deployer);

        vm.startBroadcast(deployerKey);

        // Step 1: Get USDC and deposit to Aave
        console.log("Step 1: Getting aUSDC...");

        deal(USDC, deployer, 10_000_000 * 1e6); // 10M USDC
        IERC20(USDC).approve(AAVE_POOL, type(uint256).max);
        IAavePool(AAVE_POOL).supply(USDC, 10_000_000 * 1e6, deployer, 0);

        console.log("  aUSDC:", IERC20(AUSDC).balanceOf(deployer) / 1e6);

        // Step 2: Deploy broker and mint wRLP properly
        console.log("Step 2: Minting wRLP via broker...");

        // Deploy broker
        broker = PrimeBrokerFactory(brokerFactory).createBroker(keccak256(abi.encode(block.timestamp, deployer)));

        // Transfer aUSDC to broker
        uint256 collateralAmount = 5_000_000 * 1e6; // 5M aUSDC as collateral
        IERC20(AUSDC).transfer(broker, collateralAmount);

        // Mint wRLP via broker
        uint256 wRLPToMint = 100_000 * 1e6; // 100k wRLP (6 decimals)
        PrimeBroker(payable(broker)).modifyPosition(marketId, int256(collateralAmount), int256(wRLPToMint));
        console.log("    Broker wRLP Balance:", IERC20(WRLP).balanceOf(broker));

        // Step 3: Withdraw wRLP to deployer (so we can LP directly)
        console.log("Step 3: Withdrawing tokens to deployer...");

        uint256 withdrawAmount = 50_000 * 1e6;
        PrimeBroker(payable(broker)).withdrawPositionToken(deployer, withdrawAmount);

        console.log("    Deployer wRLP Balance:", IERC20(WRLP).balanceOf(deployer));

        // Transfer remaining aUSDC from deployer to deployer (already have it)

        console.log("  Deployer aUSDC:", IERC20(AUSDC).balanceOf(deployer) / 1e6);

        // Step 4: Approve Permit2
        console.log("Step 4: Setting up Permit2 approvals...");

        IERC20(WRLP).approve(PERMIT2, type(uint256).max);
        IERC20(AUSDC).approve(PERMIT2, type(uint256).max);
        IPermit2(PERMIT2).approve(WRLP, POSM, type(uint160).max, type(uint48).max);
        IPermit2(PERMIT2).approve(AUSDC, POSM, type(uint160).max, type(uint48).max);

        console.log("  Approvals done!");

        // Step 5: Call POSM directly
        console.log("Step 5: Adding V4 liquidity directly...");

        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(WRLP),
            currency1: Currency.wrap(AUSDC),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twamm)
        });

        uint256 wRLPAmount = 10_000 * 1e6; // 10k wRLP

        // Calculate aUSDC needed
        (uint160 sqrtPriceX96, int24 currentTick,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        uint160 sqrtLower = TickMath.getSqrtPriceAtTick(TICK_LOWER);
        uint160 sqrtUpper = TickMath.getSqrtPriceAtTick(TICK_UPPER);

        console.log("  Current Tick:", currentTick);
        console.log("  Tick Lower:", TICK_LOWER);
        console.log("  Tick Upper:", TICK_UPPER);

        // Calculate Price: (sqrtPrice / 2^96)^2
        // We use 18 decimals of precision for display
        uint256 priceX18 = FullMath.mulDiv(uint256(sqrtPriceX96) * uint256(sqrtPriceX96), 1e18, 1 << 192);
        console.log("  Current Price:", priceX18 / 1e18);
        console.log("  Current Price (raw 18 dec):", priceX18);

        console.log("  Current sqrtPriceX96:", sqrtPriceX96);
        console.log("  sqrtLower:", sqrtLower);
        console.log("  sqrtUpper:", sqrtUpper);

        // Calculate liquidity for wRLP (token0)
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmount0(sqrtPriceX96, sqrtUpper, wRLPAmount);
        console.log("  Calculated Liquidity (from wRLP):", liquidity);

        // Calculate required aUSDC (token1) for that liquidity
        // amount1 = liquidity * (sqrtPrice - sqrtLower) / Q96
        // Add +1000 to cover potential rounding up by PoolManager/Aave
        uint256 aUSDCAmount = FullMath.mulDiv(uint256(liquidity), sqrtPriceX96 - sqrtLower, FixedPoint96.Q96) + 1000;

        console.log("  Calculated aUSDC needed:", aUSDCAmount);
        console.log("  Calculated aUSDC needed (readable):", aUSDCAmount / 1e6);

        bytes memory unlockData = _buildPOSMData(deployer, wRLPAmount, aUSDCAmount);

        IPositionManager(POSM).modifyLiquidities(unlockData, block.timestamp + 600);
        vm.stopBroadcast();

        console.log("  SUCCESS! LP added!");

        // Check NFT
        uint256 tokenId = IPositionManager(POSM).nextTokenId() - 1;
        console.log("  Token ID:", tokenId);

        console.log("====== FINAL STATE ======");
        console.log("Deployer wRLP:", IERC20(WRLP).balanceOf(deployer) / 1e6);
        console.log("Deployer aUSDC:", IERC20(AUSDC).balanceOf(deployer) / 1e6);
    }

    function _buildPOSMData(address recipient, uint256 wRLPAmount, uint256 aUSDCAmount)
        internal
        view
        returns (bytes memory)
    {
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(WRLP),
            currency1: Currency.wrap(AUSDC),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twamm)
        });

        // Get current sqrt price
        (uint160 sqrtPriceX96,,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());

        // Calculate liquidity
        uint160 sqrtLower = TickMath.getSqrtPriceAtTick(TICK_LOWER);
        uint160 sqrtUpper = TickMath.getSqrtPriceAtTick(TICK_UPPER);

        uint128 liquidity =
            LiquidityAmounts.getLiquidityForAmounts(sqrtPriceX96, sqrtLower, sqrtUpper, wRLPAmount, aUSDCAmount);

        console.log("  POSM Liquidity:", liquidity);

        // Actions: MINT -> SETTLE_PAIR -> TAKE_PAIR
        // TAKE_PAIR is needed because aUSDC is rebasing (interest).
        // settle() sweeps interest => negative delta (PM owes POSM).
        // TAKE_PAIR collects that credit.
        bytes memory actions =
            abi.encodePacked(uint8(Actions.MINT_POSITION), uint8(Actions.SETTLE_PAIR), uint8(Actions.TAKE_PAIR));

        bytes[] memory params = new bytes[](3);

        // 1. MINT_POSITION
        params[0] = abi.encode(
            poolKey,
            TICK_LOWER,
            TICK_UPPER,
            liquidity,
            wRLPAmount, // amount0Max (calculated from wRLP)
            aUSDCAmount * 2, // amount1Max (with buffer/slippage)
            recipient,
            bytes("") // hookData
        );

        // 2. SETTLE_PAIR
        params[1] = abi.encode(Currency.wrap(WRLP), Currency.wrap(AUSDC));

        // 3. TAKE_PAIR
        // Take potential refund/interest to recipient
        params[2] = abi.encode(
            Currency.wrap(WRLP),
            Currency.wrap(AUSDC),
            recipient,
            type(uint128).max // take everything owed
        );

        return abi.encode(actions, params);
    }
}
