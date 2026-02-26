// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {ERC20} from "solmate/src/tokens/ERC20.sol";
import {BrokerExecutor} from "../src/periphery/BrokerExecutor.sol";
import {IPrimeBroker} from "../src/shared/interfaces/IPrimeBroker.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {
    IPositionManager
} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {StateLibrary} from "v4-core/src/libraries/StateLibrary.sol";
import {Actions} from "v4-periphery/src/libraries/Actions.sol";
import {
    LiquidityAmounts
} from "@uniswap/v4-core/test/utils/LiquidityAmounts.sol";

interface IERC721 {
    function ownerOf(uint256 tokenId) external view returns (address);
    function safeTransferFrom(
        address from,
        address to,
        uint256 tokenId
    ) external;
}

interface IPermit2 {
    function approve(
        address token,
        address spender,
        uint160 amount,
        uint48 expiration
    ) external;
}

/**
 * @title TestBrokerExecutorLP
 * @notice Full LP via BrokerExecutor using packed path
 */
contract TestBrokerExecutorLP is Script {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    // V4 addresses
    address constant V4_POOL_MANAGER =
        0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant V4_POSITION_MANAGER =
        0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e;
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

    // Pool params
    int24 constant TICK_SPACING = 5;
    uint24 constant FEE = 500;

    function run() external {
        // Read from environment
        address broker = vm.envAddress("BROKER");
        address waUSDC = vm.envAddress("WAUSDC");
        address positionToken = vm.envAddress("POSITION_TOKEN");
        address twammHook = vm.envAddress("TWAMM_HOOK");
        address executorAddr = vm.envAddress("EXECUTOR");
        uint256 waUsdcAmount = vm.envUint("AUSDC_AMOUNT");
        uint256 wrlpAmount = vm.envUint("WRLP_AMOUNT");

        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        console.log("=== Full LP via BrokerExecutor ===");
        console.log("Broker:", broker);
        console.log("Executor:", executorAddr);
        console.log("waUSDC:", waUsdcAmount / 1e6);
        console.log("wRLP:", wrlpAmount / 1e6);

        BrokerExecutor executor = BrokerExecutor(executorAddr);
        IPrimeBroker pb = IPrimeBroker(broker);

        // Sort currencies
        (address currency0Addr, address currency1Addr) = waUSDC < positionToken
            ? (waUSDC, positionToken)
            : (positionToken, waUSDC);

        bool waUsdcIsCurrency0 = waUSDC < positionToken;

        PoolKey memory poolKey = PoolKey({
            currency0: Currency.wrap(currency0Addr),
            currency1: Currency.wrap(currency1Addr),
            fee: FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(twammHook)
        });

        // Get pool state
        IPoolManager pm = IPoolManager(V4_POOL_MANAGER);
        (uint160 sqrtPriceX96, int24 currentTick, , ) = pm.getSlot0(
            poolKey.toId()
        );

        // Tick range
        int24 tickLower = waUsdcIsCurrency0 ? int24(-29955) : int24(6930);
        int24 tickUpper = waUsdcIsCurrency0 ? int24(-6930) : int24(29960);
        tickLower = (tickLower / TICK_SPACING) * TICK_SPACING;
        tickUpper = (tickUpper / TICK_SPACING) * TICK_SPACING;

        // Calculate liquidity
        uint128 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPriceX96,
            TickMath.getSqrtPriceAtTick(tickLower),
            TickMath.getSqrtPriceAtTick(tickUpper),
            waUsdcIsCurrency0 ? waUsdcAmount : wrlpAmount,
            waUsdcIsCurrency0 ? wrlpAmount : waUsdcAmount
        );
        console.log("Liquidity:", liquidity);

        // Get expected next token ID
        uint256 expectedTokenId = IPositionManager(V4_POSITION_MANAGER)
            .nextTokenId();
        console.log("Expected token ID:", expectedTokenId);

        // ═══════════════════════════════════════════════════════════════════
        // BUILD THE PACKED PATH
        // ═══════════════════════════════════════════════════════════════════

        // Build LP call data
        bytes memory actions = new bytes(3);
        actions[0] = bytes1(uint8(Actions.MINT_POSITION));
        actions[1] = bytes1(uint8(Actions.CLOSE_CURRENCY));
        actions[2] = bytes1(uint8(Actions.CLOSE_CURRENCY));

        bytes[] memory params = new bytes[](3);
        params[0] = abi.encode(
            poolKey,
            tickLower,
            tickUpper,
            liquidity,
            type(uint128).max,
            type(uint128).max,
            executorAddr, // LP NFT goes to executor first
            bytes("")
        );
        params[1] = abi.encode(poolKey.currency0);
        params[2] = abi.encode(poolKey.currency1);

        bytes memory lpCalldata = abi.encodeWithSignature(
            "modifyLiquidities(bytes,uint256)",
            abi.encode(actions, params),
            block.timestamp + 1 hours
        );

        // Build packed path with 9 calls
        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](9);

        // 1. Withdraw waUSDC from broker to executor
        calls[0] = BrokerExecutor.Call({
            target: broker,
            data: abi.encodeWithSignature(
                "withdrawCollateral(address,uint256)",
                executorAddr,
                waUsdcAmount
            )
        });

        // 2. Withdraw wRLP from broker to executor
        calls[1] = BrokerExecutor.Call({
            target: broker,
            data: abi.encodeWithSignature(
                "withdrawPositionToken(address,uint256)",
                executorAddr,
                wrlpAmount
            )
        });

        // 3. Approve Permit2 for waUSDC
        calls[2] = BrokerExecutor.Call({
            target: waUSDC,
            data: abi.encodeWithSignature(
                "approve(address,uint256)",
                PERMIT2,
                type(uint256).max
            )
        });

        // 4. Approve Permit2 for wRLP
        calls[3] = BrokerExecutor.Call({
            target: positionToken,
            data: abi.encodeWithSignature(
                "approve(address,uint256)",
                PERMIT2,
                type(uint256).max
            )
        });

        // 5. Set Permit2 allowance for waUSDC -> V4 PM
        calls[4] = BrokerExecutor.Call({
            target: PERMIT2,
            data: abi.encodeWithSignature(
                "approve(address,address,uint160,uint48)",
                waUSDC,
                V4_POSITION_MANAGER,
                type(uint160).max,
                uint48(block.timestamp + 1 hours)
            )
        });

        // 6. Set Permit2 allowance for wRLP -> V4 PM
        calls[5] = BrokerExecutor.Call({
            target: PERMIT2,
            data: abi.encodeWithSignature(
                "approve(address,address,uint160,uint48)",
                positionToken,
                V4_POSITION_MANAGER,
                type(uint160).max,
                uint48(block.timestamp + 1 hours)
            )
        });

        // 7. Call V4 PM to mint LP
        calls[6] = BrokerExecutor.Call({
            target: V4_POSITION_MANAGER,
            data: lpCalldata
        });

        // 8. Transfer LP NFT from executor to broker
        calls[7] = BrokerExecutor.Call({
            target: V4_POSITION_MANAGER,
            data: abi.encodeWithSignature(
                "transferFrom(address,address,uint256)",
                executorAddr,
                broker,
                expectedTokenId
            )
        });

        // 9. Register LP for NAV calculation
        calls[8] = BrokerExecutor.Call({
            target: broker,
            data: abi.encodeWithSignature(
                "setActiveV4Position(uint256)",
                expectedTokenId
            )
        });

        console.log("Built packed path with 9 calls");

        // ═══════════════════════════════════════════════════════════════════
        // SIGN AND EXECUTE
        // ═══════════════════════════════════════════════════════════════════

        uint256 nonce = pb.operatorNonces(executorAddr);
        bytes32 callsHash = keccak256(abi.encode(calls));
        bytes32 ethSignedHash = executor.getEthSignedMessageHash(
            broker,
            nonce,
            callsHash
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(deployerKey, ethSignedHash);
        bytes memory signature = abi.encodePacked(r, s, v);

        console.log("Executing packed path...");

        vm.startBroadcast(deployerKey);
        executor.execute(broker, signature, calls);
        vm.stopBroadcast();

        // ═══════════════════════════════════════════════════════════════════
        // VERIFY
        // ═══════════════════════════════════════════════════════════════════

        address nftOwner = IERC721(V4_POSITION_MANAGER).ownerOf(
            expectedTokenId
        );
        console.log("");
        console.log("=== VERIFICATION ===");
        console.log("LP Token ID:", expectedTokenId);
        console.log("NFT Owner:", nftOwner);
        console.log("Expected broker:", broker);
        console.log("Owner matches:", nftOwner == broker);

        if (nftOwner == broker) {
            console.log("");
            console.log("=== SUCCESS ===");
            console.log("Full LP via BrokerExecutor complete!");
            console.log("LP NFT owned by broker and registered for NAV");
        } else {
            console.log("FAILED: NFT not owned by broker");
        }
    }
}
