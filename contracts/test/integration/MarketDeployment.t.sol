// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";
import "../../src/rld/core/RLDCore.sol";
import "../../src/rld/core/RLDMarketFactory.sol";
import "../../src/rld/tokens/PositionToken.sol";
import "../../src/rld/broker/PrimeBroker.sol";
import "../dex/mocks/MockPoolManager.sol";
import "../dex/mocks/MockERC20.sol";
import "../../src/rld/modules/funding/StandardFundingModel.sol";
import "../../src/rld/modules/oracles/GhostSingletonOracle.sol";

// Mock Rate oracle for initialization
contract MockRateOracle {
    function getIndexPrice(address, address) external pure returns (uint256) {
        return 1e18; // 1:1 WAD 
    }
}

contract DummyMock {
    fallback() external payable {
        assembly {
            mstore(0, 0)
            return(0, 32)
        }
    }
}

contract MarketDeploymentTest is Test {
    RLDCore core;
    RLDMarketFactory factory;
    MockPoolManager poolManager;
    PositionToken posImpl;
    PrimeBroker brokerImpl;
    MockERC20 collateral;
    MockERC20 underlying;
    StandardFundingModel fundingModel;
    MockRateOracle rateOracle;
    GhostSingletonOracle ghostOracle;
    DummyMock ghostRouter;

    function setUp() public {
        poolManager = new MockPoolManager();
        collateral = new MockERC20("aUSDC", "aUSDC", 6);
        underlying = new MockERC20("USDC", "USDC", 6);
        
        posImpl = new PositionToken("Impl", "IMPL", 6, address(collateral));
        brokerImpl = new PrimeBroker(address(0), address(0), address(0));
        fundingModel = new StandardFundingModel();
        rateOracle = new MockRateOracle();
        ghostOracle = new GhostSingletonOracle();
        ghostRouter = new DummyMock();

        // RLDMarketFactory args:
        // poolManager, positionTokenImpl, primeBrokerImpl, ghostOracle, fundingModel, ghostRouter, metadataRenderer, _fundingPeriod, brokerRouter
        factory = new RLDMarketFactory(
            address(poolManager),
            address(posImpl),
            address(brokerImpl),
            address(ghostOracle), // previously address(0x1)
            address(fundingModel),
            address(ghostRouter), // previously address(0x2)
            address(0x3000),      // MetadataRenderer Mock
            30 days,
            address(0x4000)       // BrokerRouter Mock
        );

        // Core args: factory, poolManager, twamm
        core = new RLDCore(address(factory), address(poolManager), address(0));
        factory.initializeCore(address(core));
        ghostOracle.transferOwnership(address(factory));
    }

    /// @notice The definitive Poka-Yoke Proof that prevents silent failures globally.
    function test_pokaYoke_marketDeploymentCreatesAndLinksContracts() public {
        RLDMarketFactory.DeployParams memory params = RLDMarketFactory.DeployParams({
            underlyingPool: address(0x5),
            underlyingToken: address(underlying),
            collateralToken: address(collateral),
            curator: address(this),
            positionTokenName: "Wrapped aUSDC",
            positionTokenSymbol: "waUSDC",
            minColRatio: 1.2e18,
            maintenanceMargin: 1.1e18,
            liquidationCloseFactor: 0.5e18,
            liquidationModule: address(0x6),
            fundingModel: address(0),
            fundingPeriod: 0,
            decayRateWad: 0,
            settlementModule: address(0),
            liquidationParams: bytes32(0),
            spotOracle: address(0),
            rateOracle: address(rateOracle), // Required for the pool initialization price calculation
            oraclePeriod: 3600,
            poolFee: 3000,
            tickSpacing: 60
        });

        (MarketId id, address brokerFactory) = factory.createMarket(params);
        
        // --- Hard Poka-Yoke Assumptions ---

        assertTrue(MarketId.unwrap(id) != bytes32(0), "Market ID Should NOT Be Zero");
        assertTrue(core.marketExists(id), "Core MUST Acknowledge Market");

        IRLDCore.MarketAddresses memory retrievedAddresses = core.getMarketAddresses(id);
        
        address posTkn = retrievedAddresses.positionToken;
        assertTrue(posTkn != address(0), "Position token must exist");
        assertEq(PositionToken(posTkn).owner(), address(core), "Core MUST own PositionToken precisely under singleton control");
        assertEq(PositionToken(posTkn).decimals(), 6, "wRLP Decimals MUST precisely identically match the target Collateral asset definitions");
        
        // Prove that the broker factory exists
        assertTrue(brokerFactory != address(0), "BrokerFactory MUST exist");
        
        // Assertions proving that Role Access Control cannot be manipulated
        // since only Core holds ownership permissions to direct PositionTokens
        vm.expectRevert("UNAUTHORIZED");
        PositionToken(posTkn).mint(address(this), 100);
    }
}
