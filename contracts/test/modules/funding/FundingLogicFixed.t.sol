// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, console} from "forge-std/Test.sol";
import {StandardFundingModel} from "../../../src/rld/modules/funding/StandardFundingModel.sol";
import {IRLDCore, MarketId} from "../../../src/shared/interfaces/IRLDCore.sol";
import {IRLDOracle} from "../../../src/shared/interfaces/IRLDOracle.sol";
import {ISpotOracle} from "../../../src/shared/interfaces/ISpotOracle.sol";
import {FixedPointMathLib} from "solmate/src/utils/FixedPointMathLib.sol";

// Local Mock RLD Core
contract MockRLDCore is IRLDCore {
    mapping(MarketId => MarketAddresses) public addresses;
    mapping(MarketId => MarketConfig) public configs;
    
    function setMarketAddresses(bytes32 id, MarketAddresses memory addr) external {
        addresses[MarketId.wrap(id)] = addr;
    }
    
    function setMarketConfig(bytes32 id, MarketConfig memory conf) external {
        configs[MarketId.wrap(id)] = conf;
    }

    function getMarketAddresses(MarketId id) external view override returns (MarketAddresses memory) {
        return addresses[id];
    }
    
    function getMarketConfig(MarketId id) external view override returns (MarketConfig memory) {
        return configs[id];
    }
    
    // Unimplemented functions required by interface
    function createMarket(MarketAddresses calldata, MarketConfig calldata) external override returns (MarketId) { return MarketId.wrap(0); }
    function isValidMarket(MarketId) external view override returns (bool) { return true; }
    function getMarketState(MarketId) external view override returns (MarketState memory) { return MarketState(0,0,0); }
    function getPosition(MarketId, address) external view override returns (Position memory) { return Position(0); }
    function lock(bytes calldata) external override returns (bytes memory) { return ""; }
    function lockAcquired(bytes calldata) external override returns (bytes memory) { return ""; }
    function modifyPosition(MarketId, int256, int256) external override {}
    function isSolvent(MarketId, address) external view override returns (bool) { return true; }
    function liquidate(MarketId, address, uint256) external override {}
    function proposeRiskUpdate(MarketId, uint64, uint64, uint64, uint32, uint128, bytes32) external override {}
    function cancelRiskUpdate(MarketId) external override {}
    function updatePoolFee(MarketId, uint24) external override {}
    function getPendingRiskUpdate(MarketId) external view override returns (PendingRiskUpdate memory) {
        return PendingRiskUpdate(0,0,0,0,0,bytes32(0),0,false);
    }
}

// Mock Oracles for Funding Test
contract MockRateOracle is IRLDOracle {
    uint256 public price;
    function setIndexPrice(uint256 p) external { price = p; }
    function getIndexPrice(address, address) external view returns (uint256) { return price; }
}

contract MockSpotOracle is ISpotOracle {
    uint256 public price;
    function setSpotPrice(uint256 p) external { price = p; }
    function getSpotPrice(address, address) external view returns (uint256) { return price; }
}

contract FundingLogicFixedTest is Test {
    StandardFundingModel model;
    MockRLDCore core;
    MockRateOracle rateOracle;
    MockSpotOracle spotOracle;
    
    bytes32 constant MARKET = bytes32(uint256(1));
    
    function setUp() public {
        model = new StandardFundingModel();
        core = new MockRLDCore();
        rateOracle = new MockRateOracle();
        spotOracle = new MockSpotOracle();
        
        IRLDCore.MarketAddresses memory addrs;
        addrs.rateOracle = address(rateOracle);
        addrs.markOracle = address(spotOracle); // Spot oracle used as mark oracle
        core.setMarketAddresses(MARKET, addrs);
        
        IRLDCore.MarketConfig memory config;
        config.fundingPeriod = 1 days;
        core.setMarketConfig(MARKET, config);
        
        // Warp forward to prevent timestamp underflow
        vm.warp(block.timestamp + 365 days);
    }
    
    function test_Funding_DriftingNF_StableLogic() public {
        // SCENARIO: NF has decayed to 0.5 over time
        // Index = $5.00
        // Fair Mark Price = $2.50
        
        uint256 indexPrice = 5e18; // $5.00
        uint256 nf = 0.5e18;       // 0.5
        uint256 markPrice = 2.5e18; // $2.50
        
        rateOracle.setIndexPrice(indexPrice);
        spotOracle.setSpotPrice(markPrice);
        
        // Advance time by 1 hour
        uint48 lastUpdate = uint48(block.timestamp - 1 hours);
        
        (uint256 newNF, int256 fundingRate) = model.calculateFunding(
            MARKET,
            address(core),
            nf,
            lastUpdate
        );
        
        // VERIFY:
        // 1. Normalized Mark = 2.5 / 0.5 = 5.0
        // 2. Diff = 5.0 - 5.0 = 0
        // 3. Rate = 0
        // 4. New NF = Old NF (no change)
        
        console.log("Funding Rate:", fundingRate);
        console.log("New NF:", newNF);
        
        assertEq(fundingRate, 0, "Funding should be 0");
        assertEq(newNF, nf, "NF should not change");
    }
    
    function test_Funding_Premium_With_LowNF() public {
        // SCENARIO: NF = 0.5, Index = $5.00
        // Mark = $3.00 (Premium! Fair is $2.50)
        
        uint256 indexPrice = 5e18;
        uint256 nf = 0.5e18;
        uint256 markPrice = 3e18;
        
        rateOracle.setIndexPrice(indexPrice);
        spotOracle.setSpotPrice(markPrice);
        
        // CALCULATION EXPECTED:
        // Norm Mark = 3.0 / 0.5 = 6.0
        // Index = 5.0
        // Diff = +1.0
        // Rate = 1.0 / 5.0 = +20% (0.2e18)
        
        uint48 lastUpdate = uint48(block.timestamp - 1 days); // 1 day delta = full period
        
        (uint256 newNF, int256 fundingRate) = model.calculateFunding(
            MARKET,
            address(core),
            nf, // 0.5
            lastUpdate
        );
        
        console.log("Funding Rate:", fundingRate);
        console.log("New NF:", newNF);
        
        assertEq(fundingRate, 0.2e18, "Rate should be 20%");
        
        // NF Update: NF * exp(-0.2 * 1day / 1day) = NF * exp(-0.2)
        // exp(-0.2) approx 0.8187
        // New NF approx 0.5 * 0.8187 = 0.409
        
        assertLt(newNF, nf, "NF should decrease (Shorts earn)");
    }

    function test_Funding_Discount_With_LowNF() public {
        // SCENARIO: NF = 0.5, Index = $5.00
        // Mark = $2.00 (Discount! Fair is $2.50)
        
        uint256 indexPrice = 5e18;
        uint256 nf = 0.5e18;
        uint256 markPrice = 2e18;
        
        rateOracle.setIndexPrice(indexPrice);
        spotOracle.setSpotPrice(markPrice);
        
        // CALCULATION EXPECTED:
        // Norm Mark = 2.0 / 0.5 = 4.0
        // Index = 5.0
        // Diff = -1.0
        // Rate = -1.0 / 5.0 = -20% (-0.2e18)
        
        uint48 lastUpdate = uint48(block.timestamp - 1 days);
        
        (uint256 newNF, int256 fundingRate) = model.calculateFunding(
            MARKET,
            address(core),
            nf,
            lastUpdate
        );
        
        assertEq(fundingRate, -0.2e18, "Rate should be -20%");
        assertGt(newNF, nf, "NF should increase (Longs earn)");
    }
}
