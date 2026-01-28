// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import {TwammBrokerModule} from "../../../src/rld/modules/broker/TwammBrokerModule.sol";
import {ISpotOracle} from "../../../src/shared/interfaces/ISpotOracle.sol";
import {ITWAMM} from "../../../src/twamm/ITWAMM.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency} from "v4-core/src/types/Currency.sol";

contract BrokerModuleVerificationTest is Test {
    TwammBrokerModule module;
    
    // Mocks for Oracle and TWAMM
    // We can use vm.mockCall for this since it's read-only
    
    struct ScenarioResult {
        uint256 buyOwed;
        uint256 buyPrice;
        uint256 expectedValue;
        string name;
        uint256 sellPrice;
        uint256 sellRefund;
    }

    function setUp() public {
        module = new TwammBrokerModule();
    }

    function test_VerificationFromJSON() public {
        string memory root = vm.projectRoot();
        string memory path = string.concat(root, "/test/differential/data/broker.json");
        string memory json = vm.readFile(path);
        
        bytes memory rawFuzz = vm.parseJson(json, ".fuzz");
        ScenarioResult[] memory fuzzResults = abi.decode(rawFuzz, (ScenarioResult[]));
        
        console.log("--- Verified Broker Value Fuzz Vectors (1000) ---");
        for (uint256 i = 0; i < fuzzResults.length; i++) {
            _runScenario(fuzzResults[i]);
        }
    }

    function _runScenario(ScenarioResult memory s) internal {
        address oracle = address(0x1);
        address twamm = address(0x2);
        address underlying = address(0x3);
        address sellToken = address(0x4);
        address buyToken = address(0x5);
        
        // 1. Mock TWAMM Response
        // getCancelOrderState returns (buyOwed, sellRefund)
        vm.mockCall(
            twamm,
            abi.encodeWithSelector(ITWAMM.getCancelOrderState.selector),
            abi.encode(s.buyOwed, s.sellRefund)
        );
        
        // 2. Mock Oracle Response
        // getSpotPrice(sellToken, underlying) -> sellPrice
        vm.mockCall(
            oracle,
            abi.encodeWithSelector(ISpotOracle.getSpotPrice.selector, sellToken, underlying),
            abi.encode(s.sellPrice)
        );
        // getSpotPrice(buyToken, underlying) -> buyPrice
        vm.mockCall(
            oracle,
            abi.encodeWithSelector(ISpotOracle.getSpotPrice.selector, buyToken, underlying),
            abi.encode(s.buyPrice)
        );
        
        // 3. Construct Params
        // Need dummy Keys that map to our addresses
        PoolKey memory key;
        // Logic check: TwammBrokerModule uses key.currency0/1 to find tokens.
        // Let's force it to find sellToken and buyToken.
        // if zeroForOne: sell=0, buy=1.
        key.currency0 = Currency.wrap(sellToken);
        key.currency1 = Currency.wrap(buyToken);
        
        ITWAMM.OrderKey memory orderKey;
        orderKey.zeroForOne = true;
        
        TwammBrokerModule.VerifyParams memory params = TwammBrokerModule.VerifyParams({
            hook: twamm,
            key: key,
            orderKey: orderKey,
            oracle: oracle,
            valuationToken: underlying
        });
        
        // 4. Encode & Call
        bytes memory data = abi.encode(params);
        uint256 val = module.getValue(data);
        
        assertEq(val, s.expectedValue, "Broker Value Mismatch");
    }
}
