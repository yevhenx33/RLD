// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../../src/rld/modules/funding/CDSDecayFundingModel.sol";
import "../../src/shared/interfaces/IRLDCore.sol";

contract MockCDSFundingCore {
    IRLDCore.MarketConfig internal config;

    function setDecayRate(uint96 decayRateWad) external {
        config.decayRateWad = decayRateWad;
    }

    function getMarketConfig(
        MarketId
    ) external view returns (IRLDCore.MarketConfig memory) {
        return config;
    }
}

contract CDSDecayFundingModelTest is Test {
    CDSDecayFundingModel model;
    MockCDSFundingCore core;

    MarketId constant MARKET_ID = MarketId.wrap(bytes32(uint256(1)));
    uint96 constant F_DELTA_90 = 2_302_585_092_994_045_684; // -ln(1 - 0.90)

    function setUp() public {
        model = new CDSDecayFundingModel();
        core = new MockCDSFundingCore();
    }

    function test_sameBlockReturnsCurrentFactorWithoutReadingConfig() public {
        (uint256 newFactor, int256 fundingRate) = model.calculateFunding(
            MarketId.unwrap(MARKET_ID),
            address(core),
            1e18,
            uint48(block.timestamp)
        );

        assertEq(newFactor, 1e18);
        assertEq(fundingRate, 0);
    }

    function test_revertsWhenDecayRateIsZeroAfterTimePasses() public {
        vm.warp(2 days);

        vm.expectRevert(CDSDecayFundingModel.InvalidDecayParameter.selector);
        model.calculateFunding(
            MarketId.unwrap(MARKET_ID),
            address(core),
            1e18,
            uint48(block.timestamp - 1 days)
        );
    }

    function test_revertsWhenCurrentNormalizationFactorIsZero() public {
        core.setDecayRate(F_DELTA_90);
        vm.warp(2 days);

        vm.expectRevert(CDSDecayFundingModel.InvalidNormalizationFactor.selector);
        model.calculateFunding(
            MarketId.unwrap(MARKET_ID),
            address(core),
            0,
            uint48(block.timestamp - 1 days)
        );
    }

    function test_oneYearAtDelta90DecayLeavesTenPercentCoverage() public {
        core.setDecayRate(F_DELTA_90);
        vm.warp(365 days + 1);

        (uint256 newFactor, int256 fundingRate) = model.calculateFunding(
            MarketId.unwrap(MARKET_ID),
            address(core),
            1e18,
            uint48(block.timestamp - 365 days)
        );

        assertEq(fundingRate, int256(uint256(F_DELTA_90)));
        assertApproxEqAbs(newFactor, 1e17, 1e9);
    }

    function test_revertsInsteadOfCollapsingNormalizationFactorToZero() public {
        core.setDecayRate(F_DELTA_90);
        vm.warp((20 * 365 days) + 1);

        vm.expectRevert(CDSDecayFundingModel.InvalidExponentialResult.selector);
        model.calculateFunding(
            MarketId.unwrap(MARKET_ID),
            address(core),
            1e18,
            1
        );
    }

    function test_revertsWhenRoundedNewNormalizationFactorWouldBeZero() public {
        core.setDecayRate(F_DELTA_90);
        vm.warp(365 days + 1);

        vm.expectRevert(CDSDecayFundingModel.InvalidExponentialResult.selector);
        model.calculateFunding(
            MarketId.unwrap(MARKET_ID),
            address(core),
            1,
            uint48(block.timestamp - 365 days)
        );
    }
}
