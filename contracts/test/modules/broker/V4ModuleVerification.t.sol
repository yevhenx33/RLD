// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import {UniswapV4BrokerModule} from "../../../src/rld/modules/broker/UniswapV4BrokerModule.sol";
import {ISpotOracle} from "../../../src/shared/interfaces/ISpotOracle.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IImmutableState} from "v4-periphery/src/interfaces/IImmutableState.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {IExtsload} from "v4-core/src/interfaces/IExtsload.sol";
import {PositionInfo, PositionInfoLibrary} from "v4-periphery/src/libraries/PositionInfoLibrary.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/src/types/PoolId.sol";
import {Currency} from "v4-core/src/types/Currency.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";

contract V4ModuleVerificationTest is Test {
    using PoolIdLibrary for PoolKey;
    UniswapV4BrokerModule module;
    
    struct ScenarioResult {
        uint256 expectedValue;
        uint128 liquidity;
        string name;
        uint256 price0;
        uint256 price1;
        int24 tickCurrent;
        int24 tickLower;
        int24 tickUpper;
    }

    function setUp() public {
        module = new UniswapV4BrokerModule();
    }

    function test_VerificationFromJSON() public {
        string memory root = vm.projectRoot();
        string memory path = string.concat(root, "/test/differential/data/v4.json");
        string memory json = vm.readFile(path);
        
        bytes memory rawFuzz = vm.parseJson(json, ".fuzz");
        ScenarioResult[] memory fuzzResults = abi.decode(rawFuzz, (ScenarioResult[]));
        
        console.log("--- Verified V4 Position Value Fuzz Vectors (50) ---");
        for (uint256 i = 0; i < fuzzResults.length; i++) {
            _runScenario(fuzzResults[i]);
        }
    }

    function _runScenario(ScenarioResult memory s) internal {
        address posm = address(this); // HACK: We are the PositionManager
        address pm = address(0x2);
        address oracle = address(0x3);
        address token0 = address(0x4);
        address token1 = address(0x5);
        
        // 1. Mock PositionManager.getPositionLiquidity
        // Handled by callback
        
        // 2. Mock PositionManager.getPoolAndPositionInfo
        // Handled by callback
        
        // 3. Mock PoolManager Logic (extsload)
        
        // Calculate PoolID
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(token0),
            currency1: Currency.wrap(token1),
            fee: 0,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        PoolId id = key.toId();
        
        // Calculate Slot: keccak256(abi.encodePacked(PoolId, POOLS_SLOT=6))
        bytes32 stateSlot = keccak256(abi.encodePacked(PoolId.unwrap(id), bytes32(uint256(6))));
        
        // Pack Data: (tick << 160) | sqrtPrice
        // sqrtPrice is used by TickMath? 
        // Logic: `uint160 sqrtRatioX96 = TickMath.getSqrtPriceAtTick(currentTick);`
        // Module: `(, int24 currentTick, , ) = pm.getSlot0(poolKey.toId());`
        // Module: `uint160 sqrtRatioX96 = TickMath.getSqrtPriceAtTick(currentTick);`
        // So the module IGNORES the sqrtPrice returned by Slot0 and recalculates it from Tick.
        // So we can put 0 for sqrtPrice in the packed data?
        // Note: Slot0 packing in StateLibrary:
        // sqrtPriceX96 := and(data, 0xFF..160 bits)
        // tick := signextend(2, shr(160, data))
        
        // We pack Tick at bit 160.
        // Handling negative ticks: cast to uint24 then shift?
        // Let's use abi.encodePacked logic carefuly or manual bitshifting.
        uint256 packedTick = uint256(int256(s.tickCurrent)) & 0xFFFFFF; // 24 bits
        uint256 dataVal = (packedTick << 160) | 0; // sqrtPrice=0
        
        vm.mockCall(
            pm,
            abi.encodeWithSignature("extsload(bytes32)", stateSlot),
            abi.encode(bytes32(dataVal))
        );
        
        // 4. Mock Oracle
        vm.mockCall(
            oracle,
            abi.encodeWithSelector(ISpotOracle.getSpotPrice.selector, token0, address(0)),
            abi.encode(s.price0)
        );
        vm.mockCall(
            oracle,
            abi.encodeWithSelector(ISpotOracle.getSpotPrice.selector, token1, address(0)),
            abi.encode(s.price1)
        );
        
        // 5. Construct Params
        UniswapV4BrokerModule.VerifyParams memory params = UniswapV4BrokerModule.VerifyParams({
            tokenId: 1, 
            positionManager: posm,
            oracle: oracle,
            valuationToken: address(0)
        });
        
        // Store scenario in storage for the callback
        scenario = s;
        // Also store pm address
        _poolManager = IPoolManager(pm);
        
        bytes memory data = abi.encode(params);
        
        uint256 val = module.getValue(data);
        
        assertApproxEqRel(val, s.expectedValue, 1e16, "V4 Value Mismatch");
    }
    
    // Storage for callback
    ScenarioResult scenario;
    IPoolManager _poolManager;

    function poolManager() external view returns (IPoolManager) {
        return _poolManager;
    }
    
    // Callback for IPositionManager.getPoolAndPositionInfo
    function getPoolAndPositionInfo(uint256) external view returns (PoolKey memory, PositionInfo) {
        PoolKey memory key = PoolKey({
            currency0: Currency.wrap(address(0x4)),
            currency1: Currency.wrap(address(0x5)),
            fee: 0,
            tickSpacing: 60,
            hooks: IHooks(address(0))
        });
        
        PositionInfo info = PositionInfoLibrary.initialize(key, scenario.tickLower, scenario.tickUpper);
        
        return (key, info);
    }
    
    function getPositionLiquidity(uint256) external view returns (uint128) {
        return uint128(scenario.liquidity);
    }
}
