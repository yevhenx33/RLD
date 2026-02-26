// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {console} from "forge-std/console.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
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
import {PoolId} from "@uniswap/v4-core/src/types/PoolId.sol";
import {PositionInfo} from "v4-periphery/src/libraries/PositionInfoLibrary.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";

interface IPermit2 {
    function approve(address token, address spender, uint160 amount, uint48 expiration) external;
}

interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

contract VerifyLPState is Script, StdCheats {
    using StateLibrary for IPoolManager;

    // Infrastructure
    address constant POOL_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant POSM = 0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;

    // Tokens
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
    address WRLP;

    // Pool parameters
    int24 constant TICK_LOWER = 6930; // ~$2
    int24 constant TICK_UPPER = 29960; // ~$20
    uint24 constant FEE = 500;
    int24 constant TICK_SPACING = 5;

    address twamm;
    bytes32 marketId;

    function run() external {
        string memory json = vm.readFile("./deployments.json");
        twamm = vm.parseJsonAddress(json, ".TWAMM");
        marketId = vm.parseJsonBytes32(json, ".MarketId");
        address coreAddr = vm.parseJsonAddress(json, ".RLDCore");
        address brokerFactory = vm.parseJsonAddress(json, ".BrokerFactory");

        WRLP = IRLDCore(coreAddr).getMarketAddresses(MarketId.wrap(marketId)).positionToken;

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        // We run in simulation mode to show the state changes End-to-End
        vm.startPrank(deployer);

        console.log("=== 1. SETUP: GETTING TOKENS ===");
        deal(USDC, deployer, 10_000_000 * 1e6);
        IERC20(USDC).approve(AAVE_POOL, type(uint256).max);
        IAavePool(AAVE_POOL).supply(USDC, 10_000_000 * 1e6, deployer, 0);

        address broker =
            PrimeBrokerFactory(brokerFactory).createBroker(keccak256(abi.encode(block.timestamp, deployer)));
        uint256 collateralAmount = 5_000_000 * 1e6;
        IERC20(AUSDC).transfer(broker, collateralAmount);

        uint256 wRLPToMint = 100_000 * 1e6;
        PrimeBroker(payable(broker)).modifyPosition(marketId, int256(collateralAmount), int256(wRLPToMint));
        PrimeBroker(payable(broker)).withdrawPositionToken(deployer, 50_000 * 1e6);

        console.log("=== 2. PROVISIONING LIQUIDITY ===");
        IERC20(WRLP).approve(PERMIT2, type(uint256).max);
        IERC20(AUSDC).approve(PERMIT2, type(uint256).max);
        IPermit2(PERMIT2).approve(WRLP, POSM, type(uint160).max, type(uint48).max);
        IPermit2(PERMIT2).approve(AUSDC, POSM, type(uint160).max, type(uint48).max);

        uint256 wRLPAmount = 10_000 * 1e6;
        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(WRLP),
            currency1: Currency.wrap(AUSDC),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twamm)
        });

        (uint160 sqrtPriceX96, int24 currentTick,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        uint128 liquidity =
            LiquidityAmounts.getLiquidityForAmount0(sqrtPriceX96, TickMath.getSqrtPriceAtTick(TICK_UPPER), wRLPAmount);
        uint256 aUSDCAmount = FullMath.mulDiv(
            uint256(liquidity), sqrtPriceX96 - TickMath.getSqrtPriceAtTick(TICK_LOWER), FixedPoint96.Q96
        ) + 1000;

        bytes memory unlockData = _buildPOSMData(deployer, liquidity, wRLPAmount, aUSDCAmount, poolKey);
        IPositionManager(POSM).modifyLiquidities(unlockData, block.timestamp + 600);

        uint256 tokenId = IPositionManager(POSM).nextTokenId() - 1;

        console.log("\n=== 3. VERIFICATION: POOL STATE ===");
        (sqrtPriceX96, currentTick,,) = IPoolManager(POOL_MANAGER).getSlot0(poolKey.toId());
        uint128 poolLiquidity = IPoolManager(POOL_MANAGER).getLiquidity(poolKey.toId());

        console.log("Pool ID:", vm.toString(PoolId.unwrap(poolKey.toId())));
        console.log("sqrtPriceX96:", sqrtPriceX96);
        console.log("Current Tick:", currentTick);
        console.log("Total Pool Liquidity:", poolLiquidity);

        console.log("\n=== 4. VERIFICATION: NFT STATE (ID: %s) ===", tokenId);
        uint128 posLiquidity = IPositionManager(POSM).getPositionLiquidity(tokenId);
        (PoolKey memory nftPoolKey, PositionInfo info) = IPositionManager(POSM).getPoolAndPositionInfo(tokenId);

        console.log("NFT Liquidity:", posLiquidity);
        console.log("NFT Tick Lower:", info.tickLower());
        console.log("NFT Tick Upper:", info.tickUpper());
        console.log("NFT Pool ID Match:", PoolId.unwrap(nftPoolKey.toId()) == PoolId.unwrap(poolKey.toId()));
        console.log("NFT Owner:", IERC721(POSM).ownerOf(tokenId));

        vm.stopPrank();
    }

    function _buildPOSMData(
        address recipient,
        uint128 liquidity,
        uint256 wRLPAmount,
        uint256 aUSDCAmount,
        PoolKey memory poolKey
    ) internal view returns (bytes memory) {
        bytes memory actions = abi.encodePacked(
            uint8(Actions.MINT_POSITION), uint8(Actions.SETTLE_PAIR), uint8(Actions.TAKE_PAIR)
        );

        bytes[] memory params = new bytes[](3);
        params[0] =
            abi.encode(poolKey, TICK_LOWER, TICK_UPPER, liquidity, wRLPAmount, aUSDCAmount * 2, recipient, bytes(""));
        params[1] = abi.encode(poolKey.currency0, poolKey.currency1);
        params[2] = abi.encode(poolKey.currency0, poolKey.currency1, recipient, type(uint128).max);

        return abi.encode(actions, params);
    }
}
