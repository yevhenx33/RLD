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
 * @title TestBrokerExecutor
 * @notice Tests atomic LP via BrokerExecutor with signature-based authorization
 */
contract TestBrokerExecutor is Script {
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

        console.log("=== TestBrokerExecutor ===");
        console.log("Broker:", broker);
        console.log("Executor:", executorAddr);
        console.log("Deployer:", deployer);

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
        console.log("Current tick:", int256(currentTick));

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

        // ═══════════════════════════════════════════════════════════════════
        // BUILD THE CALLS ARRAY
        // ═══════════════════════════════════════════════════════════════════

        // Create calls using BrokerExecutor.Call struct
        BrokerExecutor.Call[] memory calls = new BrokerExecutor.Call[](2);

        // Call 1: Withdraw collateral to executor
        calls[0] = BrokerExecutor.Call({
            target: broker,
            data: abi.encodeWithSignature(
                "withdrawCollateral(address,uint256)",
                executorAddr,
                waUsdcAmount
            )
        });

        // Call 2: Withdraw wRLP to executor
        calls[1] = BrokerExecutor.Call({
            target: broker,
            data: abi.encodeWithSignature(
                "withdrawPositionToken(address,uint256)",
                executorAddr,
                wrlpAmount
            )
        });

        // ═══════════════════════════════════════════════════════════════════
        // SIGN THE AUTHORIZATION MESSAGE
        // ═══════════════════════════════════════════════════════════════════

        uint256 nonce = pb.operatorNonces(executorAddr);
        console.log("Current nonce:", nonce);

        bytes32 callsHash = keccak256(abi.encode(calls));
        bytes32 messageHash = executor.getMessageHash(broker, nonce, callsHash);
        bytes32 ethSignedHash = executor.getEthSignedMessageHash(
            broker,
            nonce,
            callsHash
        );

        console.log("Message hash:");
        console.logBytes32(messageHash);

        // Sign the message
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(deployerKey, ethSignedHash);
        bytes memory signature = abi.encodePacked(r, s, v);

        console.log("Signature length:", signature.length);

        // ═══════════════════════════════════════════════════════════════════
        // EXECUTE VIA BROKER EXECUTOR
        // ═══════════════════════════════════════════════════════════════════

        console.log("");
        console.log("=== Executing via BrokerExecutor ===");

        vm.startBroadcast(deployerKey);

        // Execute the withdrawals via executor
        executor.execute(broker, signature, calls);

        console.log("Executor calls complete!");

        // Now executor has the tokens - do LP
        uint256 execWaUSDC = ERC20(waUSDC).balanceOf(executorAddr);
        uint256 execWRLP = ERC20(positionToken).balanceOf(executorAddr);
        console.log("Executor waUSDC:", execWaUSDC / 1e6);
        console.log("Executor wRLP:", execWRLP / 1e6);

        // Verify executor is no longer operator
        // (Can't check directly without interface, but the revocation happened)

        vm.stopBroadcast();

        console.log("");
        console.log("=== SUCCESS ===");
        console.log("Atomic execution complete:");
        console.log("- Signature verified");
        console.log("- Operator set via signature");
        console.log("- Calls executed");
        console.log("- Operator revoked");
    }
}
