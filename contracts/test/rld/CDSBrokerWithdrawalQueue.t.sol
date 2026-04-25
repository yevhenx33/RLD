// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import "../../src/rld/core/RLDCore.sol";
import "../../src/rld/broker/PrimeBroker.sol";
import "../../src/rld/tokens/PositionToken.sol";
import "../dex/mocks/MockERC20.sol";

contract MockBrokerRateOracle {
    function getIndexPrice(address, address) external pure returns (uint256) {
        return 5e18;
    }
}

contract MockBrokerFundingModel {
    function calculateFunding(
        bytes32,
        address,
        uint256 currentNormalizationFactor,
        uint48
    ) external pure returns (uint256 newNormalizationFactor, int256 fundingRate) {
        return (currentNormalizationFactor, 0);
    }
}

contract MockBrokerLiquidationModule {}

contract MockBrokerVerifier {
    address public broker;

    function setBroker(address broker_) external {
        broker = broker_;
    }

    function isValidBroker(address candidate) external view returns (bool) {
        return candidate == broker;
    }
}

contract MockBrokerFactory {
    address public owner;

    constructor(address owner_) {
        owner = owner_;
    }

    function ownerOf(uint256) external view returns (address) {
        return owner;
    }
}

contract CDSBrokerWithdrawalQueueTest is Test {
    using Clones for address;

    RLDCore core;
    PrimeBroker broker;
    MockERC20 collateral;
    PositionToken positionToken;
    MockBrokerVerifier verifier;
    MockBrokerFactory brokerFactory;
    MarketId marketId;
    address owner = address(this);
    address settlementModule = address(0x51);
    address constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

    function setUp() public {
        vm.etch(PERMIT2, hex"00");
        collateral = new MockERC20("USDC", "USDC", 6);
        positionToken = new PositionToken("Wrapped CDS RLP: USDC", "wCDSUSDC", 6, address(collateral));
        verifier = new MockBrokerVerifier();
        core = new RLDCore(address(this), address(0xCAFE));

        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: address(collateral),
            underlyingToken: address(collateral),
            underlyingPool: address(0x1003),
            rateOracle: address(new MockBrokerRateOracle()),
            spotOracle: address(0),
            markOracle: address(0x1005),
            fundingModel: address(new MockBrokerFundingModel()),
            curator: address(this),
            liquidationModule: address(new MockBrokerLiquidationModule()),
            positionToken: address(positionToken),
            settlementModule: settlementModule
        });

        IRLDCore.MarketConfig memory config = IRLDCore.MarketConfig({
            minColRatio: uint64(1.2e18),
            maintenanceMargin: uint64(1.1e18),
            liquidationCloseFactor: uint64(0.5e18),
            fundingPeriod: 30 days,
            badDebtPeriod: 7 days,
            debtCap: type(uint128).max,
            minLiquidation: 0,
            liquidationParams: bytes32(0),
            decayRateWad: uint96(2.3e18),
            brokerVerifier: address(verifier)
        });

        marketId = core.createMarket(addresses, config);
        positionToken.setMarketId(marketId);
        positionToken.transferOwnership(address(core));

        brokerFactory = new MockBrokerFactory(owner);
        PrimeBroker brokerImpl = new PrimeBroker(address(0), address(0), address(0));
        broker = PrimeBroker(payable(address(brokerImpl).clone()));
        address[] memory operators = new address[](0);
        broker.initialize(marketId, address(brokerFactory), address(core), operators);
        verifier.setBroker(address(broker));

        collateral.mint(address(broker), 100_000_000e6);
        broker.modifyPosition(MarketId.unwrap(marketId), 0, int256(1_000_000e6));
    }

    function test_debtBearingCdsBrokerCanWithdrawPositionTokenButNotCollateral() public {
        uint256 positionBalance = positionToken.balanceOf(address(broker));
        assertGt(positionBalance, 0);

        broker.withdrawToken(address(positionToken), owner, positionBalance);
        assertEq(positionToken.balanceOf(owner), positionBalance);

        vm.expectRevert(PrimeBroker.WithdrawalQueueRequired.selector);
        broker.withdrawToken(address(collateral), owner, 1e6);
    }
}
