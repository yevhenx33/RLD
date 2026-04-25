// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../../src/rld/core/RLDCore.sol";
import "../../src/rld/modules/settlement/CDSSettlementProxy.sol";

contract MockSettlementCore {
    MarketId public lastSettledMarketId;
    MarketId public lastInvalidatedMarketId;
    address public lastInvalidatedBroker;
    uint256 public settlementCalls;
    uint256 public invalidationCalls;

    function enterGlobalSettlement(MarketId marketId) external {
        lastSettledMarketId = marketId;
        settlementCalls++;
    }

    function invalidateBrokerWithdrawalQueue(
        MarketId marketId,
        address broker
    ) external {
        lastInvalidatedMarketId = marketId;
        lastInvalidatedBroker = broker;
        invalidationCalls++;
    }
}

contract DummyBroker {}

contract MockBrokerVerifier {
    mapping(address => bool) public validBrokers;

    function setValid(address broker, bool valid) external {
        validBrokers[broker] = valid;
    }

    function isValidBroker(address broker) external view returns (bool) {
        return validBrokers[broker];
    }
}

contract MockPrimeBroker {
    uint64 public queueEpoch;

    function invalidateWithdrawalQueue() external returns (uint64 newQueueEpoch) {
        newQueueEpoch = queueEpoch + 1;
        queueEpoch = newQueueEpoch;
    }
}

contract CDSSettlementProxyTest is Test {
    MockSettlementCore core;
    CDSSettlementProxy proxy;
    address owner = address(0xA11CE);
    address operator = address(0x0F);
    address nonOwner = address(0xB0B);
    MarketId constant MARKET_ID = MarketId.wrap(bytes32(uint256(123)));

    function setUp() public {
        core = new MockSettlementCore();
        proxy = new CDSSettlementProxy(address(core), owner);
    }

    function test_constructorRejectsZeroOrNonContractCore() public {
        vm.expectRevert(CDSSettlementProxy.InvalidCore.selector);
        new CDSSettlementProxy(address(0), owner);

        vm.expectRevert(CDSSettlementProxy.InvalidCore.selector);
        new CDSSettlementProxy(address(0x1234), owner);
    }

    function test_constructorRejectsZeroOwner() public {
        vm.expectRevert(CDSSettlementProxy.InvalidOwner.selector);
        new CDSSettlementProxy(address(core), address(0));
    }

    function test_ownerCanEmergencyEnterGlobalSettlement() public {
        vm.prank(owner);
        proxy.enterGlobalSettlement(MARKET_ID);

        assertEq(MarketId.unwrap(core.lastSettledMarketId()), MarketId.unwrap(MARKET_ID));
        assertEq(core.settlementCalls(), 1);
    }

    function test_nonOwnerCannotEnterGlobalSettlement() public {
        vm.prank(nonOwner);
        vm.expectRevert("UNAUTHORIZED");
        proxy.enterGlobalSettlement(MARKET_ID);
    }

    function test_ownerCanAllowlistOperator() public {
        vm.prank(owner);
        proxy.setOperator(operator, true);

        assertTrue(proxy.operators(operator));
    }

    function test_nonOwnerCannotAllowlistOperator() public {
        vm.prank(nonOwner);
        vm.expectRevert("UNAUTHORIZED");
        proxy.setOperator(operator, true);
    }

    function test_operatorCanSubmitSettlementAttestation() public {
        vm.prank(owner);
        proxy.setOperator(operator, true);

        uint8 trackMask = proxy.TRACK_UTILIZATION_FREEZE() |
            proxy.TRACK_BAD_DEBT_ACCRUAL();
        bytes32 dataRoot = keccak256("symbiotic-payload-root");
        bytes memory operatorData = abi.encode("placeholder attestation");
        uint64 observedAt = uint64(block.timestamp);

        vm.prank(operator);
        bytes32 attestationHash = proxy.submitSettlementAttestation(
            MARKET_ID,
            trackMask,
            observedAt,
            dataRoot,
            operatorData
        );

        assertTrue(proxy.consumedAttestations(attestationHash));
        assertEq(MarketId.unwrap(core.lastSettledMarketId()), MarketId.unwrap(MARKET_ID));
        assertEq(core.settlementCalls(), 1);
    }

    function test_ownerCanSubmitSettlementAttestationWithoutOperatorFlag() public {
        uint8 trackMask = proxy.TRACK_UTILIZATION_FREEZE() |
            proxy.TRACK_COLLATERAL_COLLAPSE();

        vm.prank(owner);
        proxy.submitSettlementAttestation(
            MARKET_ID,
            trackMask,
            uint64(block.timestamp),
            keccak256("owner-root"),
            ""
        );

        assertEq(core.settlementCalls(), 1);
    }

    function test_unauthorizedOperatorCannotSubmitAttestation() public {
        uint8 trackMask = proxy.TRACK_UTILIZATION_FREEZE() |
            proxy.TRACK_BAD_DEBT_ACCRUAL();

        vm.prank(nonOwner);
        vm.expectRevert(CDSSettlementProxy.UnauthorizedOperator.selector);
        proxy.submitSettlementAttestation(
            MARKET_ID,
            trackMask,
            uint64(block.timestamp),
            keccak256("root"),
            ""
        );
    }

    function test_attestationRequiresValidTwoOfThreeTrackMask() public {
        vm.startPrank(owner);

        vm.expectRevert(CDSSettlementProxy.InvalidTrackMask.selector);
        proxy.submitSettlementAttestation(MARKET_ID, 0, uint64(block.timestamp), bytes32(0), "");

        vm.expectRevert(CDSSettlementProxy.InvalidTrackMask.selector);
        proxy.submitSettlementAttestation(MARKET_ID, 8, uint64(block.timestamp), bytes32(0), "");

        uint8 singleTrack = proxy.TRACK_UTILIZATION_FREEZE();
        vm.expectRevert(CDSSettlementProxy.InsufficientSettlementTracks.selector);
        proxy.submitSettlementAttestation(
            MARKET_ID,
            singleTrack,
            uint64(block.timestamp),
            bytes32(0),
            ""
        );

        vm.stopPrank();
    }

    function test_attestationRejectsInvalidObservationTimestamp() public {
        uint8 trackMask = proxy.TRACK_UTILIZATION_FREEZE() |
            proxy.TRACK_COLLATERAL_COLLAPSE();

        vm.startPrank(owner);

        vm.expectRevert(CDSSettlementProxy.InvalidObservationTimestamp.selector);
        proxy.submitSettlementAttestation(MARKET_ID, trackMask, 0, bytes32(0), "");

        vm.expectRevert(CDSSettlementProxy.InvalidObservationTimestamp.selector);
        proxy.submitSettlementAttestation(
            MARKET_ID,
            trackMask,
            uint64(block.timestamp + 1),
            bytes32(0),
            ""
        );

        vm.stopPrank();
    }

    function test_attestationReplayIsRejectedIfCoreAllowsFirstCall() public {
        uint8 trackMask = proxy.TRACK_UTILIZATION_FREEZE() |
            proxy.TRACK_COLLATERAL_COLLAPSE();
        uint64 observedAt = uint64(block.timestamp);
        bytes32 dataRoot = keccak256("root");
        bytes memory operatorData = "payload";

        vm.startPrank(owner);
        proxy.submitSettlementAttestation(MARKET_ID, trackMask, observedAt, dataRoot, operatorData);

        vm.expectRevert(CDSSettlementProxy.AttestationAlreadyConsumed.selector);
        proxy.submitSettlementAttestation(MARKET_ID, trackMask, observedAt, dataRoot, operatorData);
        vm.stopPrank();
    }

    function test_ownerCanInvalidateSingleBrokerQueue() public {
        address broker = address(new DummyBroker());

        vm.prank(owner);
        proxy.invalidateBrokerWithdrawalQueue(MARKET_ID, broker);

        assertEq(MarketId.unwrap(core.lastInvalidatedMarketId()), MarketId.unwrap(MARKET_ID));
        assertEq(core.lastInvalidatedBroker(), broker);
        assertEq(core.invalidationCalls(), 1);
    }

    function test_operatorCanInvalidateSingleBrokerQueue() public {
        address broker = address(new DummyBroker());

        vm.prank(owner);
        proxy.setOperator(operator, true);

        vm.prank(operator);
        proxy.invalidateBrokerWithdrawalQueue(MARKET_ID, broker);

        assertEq(core.lastInvalidatedBroker(), broker);
    }

    function test_invalidateRejectsZeroOrNonContractBroker() public {
        vm.startPrank(owner);

        vm.expectRevert(CDSSettlementProxy.InvalidBroker.selector);
        proxy.invalidateBrokerWithdrawalQueue(MARKET_ID, address(0));

        vm.expectRevert(CDSSettlementProxy.InvalidBroker.selector);
        proxy.invalidateBrokerWithdrawalQueue(MARKET_ID, address(0x1234));

        vm.stopPrank();
    }

    function test_ownerCanBatchInvalidateBrokerQueues() public {
        address[] memory brokers = new address[](2);
        brokers[0] = address(new DummyBroker());
        brokers[1] = address(new DummyBroker());

        vm.prank(owner);
        proxy.invalidateBrokerWithdrawalQueues(MARKET_ID, brokers);

        assertEq(core.invalidationCalls(), 2);
        assertEq(core.lastInvalidatedBroker(), brokers[1]);
    }
}

contract CDSSettlementProxyCoreIntegrationTest is Test {
    RLDCore core;
    CDSSettlementProxy proxy;
    MockBrokerVerifier verifier;
    address owner = address(0xA11CE);
    address operator = address(0x0F);
    MarketId marketId;

    function setUp() public {
        core = new RLDCore(address(this), address(0xCAFE));
        proxy = new CDSSettlementProxy(address(core), owner);
        verifier = new MockBrokerVerifier();

        IRLDCore.MarketAddresses memory addresses = IRLDCore.MarketAddresses({
            collateralToken: address(0x1001),
            underlyingToken: address(0x1002),
            underlyingPool: address(0x1003),
            rateOracle: address(0x1004),
            spotOracle: address(0),
            markOracle: address(0x1005),
            fundingModel: address(0x1006),
            curator: address(this),
            liquidationModule: address(0x1007),
            positionToken: address(0x1008),
            settlementModule: address(proxy)
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
    }

    function test_onlyConfiguredSettlementModuleCanEnterSettlement() public {
        vm.expectRevert(IRLDCore.Unauthorized.selector);
        core.enterGlobalSettlement(marketId);

        vm.prank(owner);
        proxy.enterGlobalSettlement(marketId);

        assertEq(core.getMarketState(marketId).globalSettlementTimestamp, block.timestamp);
    }

    function test_operatorAttestationCanEnterSettlementThroughConfiguredProxy() public {
        vm.prank(owner);
        proxy.setOperator(operator, true);
        uint8 trackMask = proxy.TRACK_UTILIZATION_FREEZE() |
            proxy.TRACK_BAD_DEBT_ACCRUAL();

        vm.prank(operator);
        proxy.submitSettlementAttestation(
            marketId,
            trackMask,
            uint64(block.timestamp),
            keccak256("symbiotic-root"),
            "operator payload"
        );

        assertEq(core.getMarketState(marketId).globalSettlementTimestamp, block.timestamp);
    }

    function test_moduleInvalidatesBrokerQueueThroughCoreAfterSettlement() public {
        MockPrimeBroker broker = new MockPrimeBroker();
        verifier.setValid(address(broker), true);

        vm.startPrank(owner);
        proxy.enterGlobalSettlement(marketId);
        proxy.invalidateBrokerWithdrawalQueue(marketId, address(broker));
        vm.stopPrank();

        assertEq(broker.queueEpoch(), 1);
    }
}
